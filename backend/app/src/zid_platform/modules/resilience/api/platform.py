"""Platform recovery operations API: Operator Console only (ZID-RESILIENCE-001 §15-20, §25-30;
ZID-TENANCY-001 §15; UX §21; G-W7).

    GET  /platform/cells/{cell_id}                     state and latest readiness
    POST /platform/cells/{cell_id}:quarantine          no new placement; not a recovery target
    POST /platform/cells/{cell_id}:restore             back to ACTIVE
    PUT  /platform/cells/{cell_id}/readiness           replica lag, backup age, keys, headroom
    GET  /platform/recovery-readiness?cells=a,b        readiness of the listed cells
    POST /platform/tenants/{tenant_id}:failover        declare a cell-disaster failover
    POST /platform/tenants/{tenant_id}:migrate         a governed cell migration
    POST /platform/tenants/{tenant_id}:failback        a planned move back after a failover
    POST /platform/restore-jobs                        a restore into an isolated environment
    GET  /platform/operations[/{id}]                   operations with their runbook and step log
    POST /platform/operations/{id}:advance             run the next step (in order, with evidence)
    POST /platform/operations/{id}:abort               before cutover only
    GET  /platform/dr-exercises
    POST /platform/dr-exercises                        record an exercise result

Every endpoint sits behind the operator gate (operator client and tenant, fresh AAL-Z3, no
delegation); every mutation also needs an ACTIVE JIT grant for its capability, re-checked per call.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from zid_context import RequestContext
from zid_db import tenant_transaction
from zid_errors import ZidError

from zid_platform.api_auth import request_engine
from zid_platform.modules.operator_access.ports import require_operator
from zid_platform.modules.resilience.application import service
from zid_platform.modules.resilience.application.service import Actor, ResilienceSettings
from zid_platform.modules.resilience.domain.runbooks import (
    RUNBOOKS,
    Kind,
    OpStatus,
    ResilienceError,
)
from zid_platform.modules.resilience.infrastructure import store
from zid_platform.modules.resilience.infrastructure.store import Operation

router = APIRouter(prefix="/platform", tags=["resilience"])
OperatorDep = Annotated[Any, Depends(require_operator)]
_CELL = re.compile(r"[a-z0-9-]{1,40}")
CellId = Annotated[str, Field(pattern=r"^[a-z0-9-]{1,40}$")]

_STATUS = {
    "RECOVERY_OPERATION_NOT_FOUND": 404,
    "OPERATOR_CAPABILITY_REQUIRED": 403,
    "RECOVERY_TENANT_REQUIRED": 422,
    "RECOVERY_TARGET_INVALID": 422,
    "RECOVERY_EVIDENCE_REQUIRED": 422,
    "RESIDENCY_FAILOVER_BLOCKED": 409,
    "CELL_UNAVAILABLE": 503,
}


def http(e: ResilienceError) -> ZidError:
    return ZidError(
        e.code,
        e.detail or e.code.replace("_", " ").capitalize() + ".",
        http_status=_STATUS.get(e.code, 409),
    )


def _settings(request: Request) -> ResilienceSettings:
    s: ResilienceSettings | None = getattr(request.app.state, "resilience_settings", None)
    if s is None or not s.operator_tenant_id:
        raise ZidError(
            "OPERATOR_PLANE_DISABLED",
            "No operator tenant is configured in this cell.",
            http_status=403,
        )
    return s


def _actor(request: Request, op: Any) -> tuple[Actor, RequestContext]:
    ctx: RequestContext = request.state.operator_ctx
    return Actor(op.principal_id, op.tenant_id), ctx


def _iso(d: datetime | None) -> str | None:
    return d.isoformat() if d else None


def op_view(op: Operation, log: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    runbook = RUNBOOKS[op.kind]
    nxt = (
        runbook[op.steps_done]
        if op.steps_done < len(runbook) and op.status in (OpStatus.OPEN, OpStatus.BLOCKED)
        else None
    )
    out: dict[str, Any] = {
        "operation_id": op.operation_id,
        "tenant_id": op.tenant_id,
        "kind": op.kind.value,
        "source_cell_id": op.source_cell_id,
        "target_cell_id": op.target_cell_id,
        "status": op.status.value,
        "blocked_code": op.blocked_code,
        "steps_done": op.steps_done,
        "next_step": {"name": nxt.name, "type": nxt.type.value, "detail": nxt.detail}
        if nxt
        else None,
        "runbook": [
            {"name": s.name, "type": s.type.value, "detail": s.detail, "done": i < op.steps_done}
            for i, s in enumerate(runbook)
        ],
        "epoch_before": op.epoch_before,
        "epoch_after": op.epoch_after,
        "declared_by": op.declared_by,
        "declared_at": _iso(op.declared_at),
        "completed_at": _iso(op.completed_at),
        "observed_rto_s": op.observed_rto_s,
    }
    if log is not None:
        out["steps"] = [
            {
                "seq": r["seq"],
                "step": r["step"],
                "outcome": r["outcome"],
                "actor": str(r["actor"]) if r["actor"] else None,
                "evidence_ref": r["evidence_ref"],
                "report": r["report"],
                "at": _iso(r["at"]),
            }
            for r in log
        ]
    return out


# ------------------------------------------------------------------------------------ bodies


class ReasonBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=4, max_length=2000)


class ReadinessBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    replica_lag_s: float | None = Field(default=None, ge=0)
    backup_age_s: float | None = Field(default=None, ge=0)
    recovery_keys_ready: bool = False
    one_az_loss_headroom: float | None = Field(default=None, ge=0, le=1)
    last_restore_verified: datetime | None = None


class MoveBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_cell_id: CellId


class RestoreBody(MoveBody):
    target_tenant_id: uuid.UUID | None = None


class AdvanceBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    step: str = Field(pattern=r"^[A-Z_]{2,40}$")
    evidence_ref: str | None = Field(default=None, min_length=1, max_length=256)
    probes: dict[str, bool] | None = None


class ExerciseBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal[
        "FAILOVER",
        "MIGRATION",
        "FAILBACK",
        "RESTORE",
        "AZ_LOSS",
        "PROCESS_KILL",
        "BROKER_DEGRADATION",
        "KMS_OUTAGE",
        "CONTROL_PLANE_LOSS",
        "PARTITION",
    ]
    exercised_cell_id: CellId
    scenario: str = Field(min_length=4, max_length=500)
    operation_id: uuid.UUID | None = None
    observed_rto_s: float | None = Field(default=None, ge=0)
    observed_rpo_s: float | None = Field(default=None, ge=0)
    findings: list[Annotated[str, Field(max_length=500)]] = Field(
        default_factory=list, max_length=50
    )
    started_at: datetime
    completed_at: datetime


# ------------------------------------------------------------------------------------- cells


def _cell(cell_id: str) -> str:
    if not _CELL.fullmatch(cell_id):
        raise ZidError(
            "CELL_ID_INVALID",
            "A cell id is lowercase letters, digits and hyphens.",
            http_status=422,
        )
    return cell_id


@router.get("/cells/{cell_id}")
async def get_cell(cell_id: str, request: Request, op: OperatorDep) -> dict[str, Any]:
    _settings(request)
    _, ctx = _actor(request, op)
    async with tenant_transaction(request_engine(request), ctx) as conn:
        r = await store.readiness(conn, _cell(cell_id))
        status = await store.cell_status(conn, cell_id)
    return r.view() if r else {"cell_id": cell_id, "status": status, "reported": False}


async def _set_status(
    request: Request, op: Any, cell_id: str, status: str, reason: str
) -> dict[str, Any]:
    _settings(request)
    actor, ctx = _actor(request, op)
    try:
        async with tenant_transaction(request_engine(request), ctx) as conn:
            previous = await service.set_cell_status(
                conn, actor, _cell(cell_id), status, reason=reason, now=datetime.now(UTC)
            )
    except ResilienceError as e:
        raise http(e) from e
    return {"cell_id": cell_id, "previous_status": previous, "status": status}


@router.post("/cells/{cell_id}:quarantine")
async def quarantine_cell(
    cell_id: str, body: ReasonBody, request: Request, op: OperatorDep
) -> dict[str, Any]:
    return await _set_status(request, op, cell_id, "QUARANTINED", body.reason)


@router.post("/cells/{cell_id}:restore")
async def restore_cell(
    cell_id: str, body: ReasonBody, request: Request, op: OperatorDep
) -> dict[str, Any]:
    return await _set_status(request, op, cell_id, "ACTIVE", body.reason)


@router.put("/cells/{cell_id}/readiness")
async def put_readiness(
    cell_id: str, body: ReadinessBody, request: Request, op: OperatorDep
) -> dict[str, Any]:
    _settings(request)
    actor, ctx = _actor(request, op)
    try:
        async with tenant_transaction(request_engine(request), ctx) as conn:
            await service.report_readiness(
                conn, actor, _cell(cell_id), body.model_dump(), datetime.now(UTC)
            )
            r = await store.readiness(conn, cell_id)
    except ResilienceError as e:
        raise http(e) from e
    assert r is not None
    return r.view()


@router.get("/recovery-readiness")
async def recovery_readiness(
    request: Request, op: OperatorDep, cells: Annotated[str, Query(max_length=800)]
) -> dict[str, Any]:
    _settings(request)
    _, ctx = _actor(request, op)
    out = []
    async with tenant_transaction(request_engine(request), ctx) as conn:
        for c in [_cell(x.strip()) for x in cells.split(",") if x.strip()][:20]:
            r = await store.readiness(conn, c)
            out.append(
                r.view()
                if r
                else {"cell_id": c, "status": await store.cell_status(conn, c), "reported": False}
            )
    return {"cells": out}


# -------------------------------------------------------------------------------- operations


async def _declare(
    request: Request, op: Any, kind: Kind, tenant: str | None, target: str
) -> dict[str, Any]:
    s = _settings(request)
    actor, ctx = _actor(request, op)
    try:
        async with tenant_transaction(request_engine(request), ctx) as conn:
            o = await service.declare(
                conn,
                s,
                actor,
                kind=kind,
                tenant_id=tenant,
                target_cell_id=target,
                now=datetime.now(UTC),
            )
    except ResilienceError as e:
        raise http(e) from e
    return op_view(o, [])


@router.post("/tenants/{tenant_id}:failover", status_code=201)
async def failover(
    tenant_id: uuid.UUID, body: MoveBody, request: Request, op: OperatorDep
) -> dict[str, Any]:
    return await _declare(request, op, Kind.FAILOVER, str(tenant_id), body.target_cell_id)


@router.post("/tenants/{tenant_id}:migrate", status_code=201)
async def migrate(
    tenant_id: uuid.UUID, body: MoveBody, request: Request, op: OperatorDep
) -> dict[str, Any]:
    return await _declare(request, op, Kind.MIGRATION, str(tenant_id), body.target_cell_id)


@router.post("/tenants/{tenant_id}:failback", status_code=201)
async def failback(
    tenant_id: uuid.UUID, body: MoveBody, request: Request, op: OperatorDep
) -> dict[str, Any]:
    return await _declare(request, op, Kind.FAILBACK, str(tenant_id), body.target_cell_id)


@router.post("/restore-jobs", status_code=201)
async def restore_job(body: RestoreBody, request: Request, op: OperatorDep) -> dict[str, Any]:
    return await _declare(
        request,
        op,
        Kind.RESTORE,
        str(body.target_tenant_id) if body.target_tenant_id else None,
        body.target_cell_id,
    )


@router.get("/operations")
async def list_operations(request: Request, op: OperatorDep) -> dict[str, Any]:
    _settings(request)
    _, ctx = _actor(request, op)
    async with tenant_transaction(request_engine(request), ctx) as conn:
        ops = await store.operations(conn)
    return {"operations": [op_view(o) for o in ops]}


@router.get("/operations/{operation_id}")
async def get_operation(
    operation_id: uuid.UUID, request: Request, op: OperatorDep
) -> dict[str, Any]:
    _settings(request)
    _, ctx = _actor(request, op)
    async with tenant_transaction(request_engine(request), ctx) as conn:
        o = await store.load_operation(conn, str(operation_id))
        log = await store.steps(conn, str(operation_id)) if o else []
    if o is None:
        raise http(ResilienceError("RECOVERY_OPERATION_NOT_FOUND", "No such recovery operation."))
    return op_view(o, log)


@router.post("/operations/{operation_id}:advance")
async def advance(
    operation_id: uuid.UUID, body: AdvanceBody, request: Request, op: OperatorDep
) -> dict[str, Any]:
    s = _settings(request)
    actor, ctx = _actor(request, op)
    try:
        async with tenant_transaction(request_engine(request), ctx) as conn:
            r = await service.advance(
                conn,
                s,
                actor,
                str(operation_id),
                step=body.step,
                evidence_ref=body.evidence_ref,
                probes=body.probes,
                now=datetime.now(UTC),
            )
            log = await store.steps(conn, str(operation_id))
    except ResilienceError as e:
        raise http(e) from e
    if r.operation.status is OpStatus.BLOCKED:  # committed: the block and its report are on record
        failed = [g for g in r.report if g["status"] == "FAIL"]
        raise ZidError(
            r.operation.blocked_code or "RECOVERY_BLOCKED",
            "; ".join(f"{g['gate_id']}: {g['detail']}" for g in failed)
            or "A recovery gate failed.",
            http_status=_STATUS.get(r.operation.blocked_code or "", 409),
        )
    return {**op_view(r.operation, log), "report": r.report}


@router.post("/operations/{operation_id}:abort")
async def abort(
    operation_id: uuid.UUID, body: ReasonBody, request: Request, op: OperatorDep
) -> dict[str, Any]:
    s = _settings(request)
    actor, ctx = _actor(request, op)
    try:
        async with tenant_transaction(request_engine(request), ctx) as conn:
            o = await service.abort(
                conn, s, actor, str(operation_id), reason=body.reason, now=datetime.now(UTC)
            )
    except ResilienceError as e:
        raise http(e) from e
    return op_view(o)


# --------------------------------------------------------------------------------- exercises


@router.get("/dr-exercises")
async def list_exercises(request: Request, op: OperatorDep) -> dict[str, Any]:
    _settings(request)
    _, ctx = _actor(request, op)
    async with tenant_transaction(request_engine(request), ctx) as conn:
        rows = await store.exercises(conn)
    return {
        "exercises": [
            {
                k: (
                    str(v)
                    if isinstance(v, uuid.UUID)
                    else _iso(v)
                    if isinstance(v, datetime)
                    else float(v)
                    if v is not None and k.endswith("_s")
                    else v
                )
                for k, v in r.items()
            }
            for r in rows
        ]
    }


@router.post("/dr-exercises", status_code=201)
async def record_exercise(body: ExerciseBody, request: Request, op: OperatorDep) -> dict[str, Any]:
    _settings(request)
    actor, ctx = _actor(request, op)
    if body.completed_at < body.started_at:
        raise ZidError(
            "EXERCISE_WINDOW_INVALID", "completed_at is before started_at.", http_status=422
        )
    try:
        async with tenant_transaction(request_engine(request), ctx) as conn:
            return await service.record_exercise(
                conn,
                actor,
                kind=body.kind,
                cell_id=body.exercised_cell_id,
                scenario=body.scenario,
                operation_id=str(body.operation_id) if body.operation_id else None,
                observed_rto_s=body.observed_rto_s,
                observed_rpo_s=body.observed_rpo_s,
                findings=body.findings,
                started_at=body.started_at,
                completed_at=body.completed_at,
                now=datetime.now(UTC),
            )
    except ResilienceError as e:
        raise http(e) from e
