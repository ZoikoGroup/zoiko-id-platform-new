"""Operational control API (ZID-SRE-001 §29; Operator Console only; G-W8).

    GET  /ops/services                               the production service catalogue
    GET  /ops/services/{id}/slos                     SLI/SLO/error-budget state (this process's windows)
    GET  /ops/services/{id}/health                   composite operational state (§30)
    GET  /ops/services/{id}/change-policy?change_class=STANDARD|RISKY|FIX|SECURITY
    GET  /ops/services/{id}/readiness                latest PRR verdict, re-evaluated today
    POST /ops/services/{id}:freeze-changes           any active operator (protective)
    POST /ops/services/{id}:unfreeze-changes         needs an ACTIVE platform.change.manage grant
    GET  /ops/cells/{id}/health                      cell state, readiness and recovery in progress
    POST /ops/incidents                              declare (no approval step, §16.1)
    GET  /ops/incidents[?open=true]
    GET  /ops/incidents/{id}                         with its timeline and action items
    PATCH /ops/incidents/{id}                        severity / status / roles
    POST /ops/incidents/{id}/actions                 append an action, decision or communication
    POST /ops/incidents/{id}:resolve                 after validation
    POST /ops/incidents/{id}/action-items            PIR action items
    POST /ops/incidents/{id}/action-items/{item}:verify   independent verification
    POST /ops/incidents/{id}:close                   SEV0/SEV1: PIR + verified items
    POST /ops/readiness-reviews                      record a PRR (§24)

DR exercises and recovery operations live under /platform (resilience module).
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, date, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from zid_context import RequestContext
from zid_db import tenant_transaction
from zid_errors import ZidError
from zid_telemetry.slo import ChangeClass

from zid_platform.api_auth import request_engine
from zid_platform.modules.operations.application import service
from zid_platform.modules.operations.application.service import Actor
from zid_platform.modules.operations.domain.catalogue import Catalogue, Service
from zid_platform.modules.operations.domain.incidents import (
    GateResult,
    GateStatus,
    IncidentStatus,
    OperationsError,
    Severity,
    operational_state,
)
from zid_platform.modules.operations.infrastructure import store
from zid_platform.modules.operations.infrastructure.store import Incident
from zid_platform.modules.operator_access.ports import require_operator
from zid_platform.modules.resilience.ports import cell_view, operation_in_progress

router = APIRouter(prefix="/ops", tags=["operations"])
OperatorDep = Annotated[Any, Depends(require_operator)]
_CELL = re.compile(r"[a-z0-9-]{1,40}")

_STATUS = {
    "INCIDENT_NOT_FOUND": 404,
    "SERVICE_UNKNOWN": 404,
    "PIR_ACTION_NOT_FOUND": 404,
    "OPERATOR_PROFILE_MISSING": 403,
    "OPERATOR_CAPABILITY_REQUIRED": 403,
    "PIR_VERIFICATION_NOT_INDEPENDENT": 403,
    "INCIDENT_ROLE_UNKNOWN": 422,
    "INCIDENT_ENTRY_KIND": 422,
    "INCIDENT_VALIDATION_REQUIRED": 422,
    "PRR_GATE_UNKNOWN": 422,
    "PRR_GATE_DUPLICATED": 422,
    "PRR_EVIDENCE_REQUIRED": 422,
    "PRR_ACCEPTANCE_INCOMPLETE": 422,
}


def http(e: OperationsError) -> ZidError:
    return ZidError(
        e.code,
        e.detail or e.code.replace("_", " ").capitalize() + ".",
        http_status=_STATUS.get(e.code, 409),
    )


def _cat(request: Request) -> Catalogue:
    cat: Catalogue | None = getattr(request.app.state, "ops_catalogue", None)
    if cat is None:
        raise ZidError(
            "OPS_CATALOGUE_UNAVAILABLE", "No service catalogue is loaded.", http_status=503
        )
    return cat


def _svc(request: Request, service_id: str) -> Service:
    svc = _cat(request).service(service_id)
    if svc is None:
        raise http(OperationsError("SERVICE_UNKNOWN", service_id))
    return svc


def _actor(request: Request, op: Any) -> tuple[Actor, RequestContext]:
    ctx: RequestContext = request.state.operator_ctx
    return Actor(op.principal_id, op.tenant_id), ctx


def _iso(d: datetime | date | None) -> str | None:
    return d.isoformat() if d else None


def inc_view(
    i: Incident, log: list[dict[str, Any]] | None = None, items: list[Any] | None = None
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "incident_id": i.incident_id,
        "title": i.title,
        "severity": i.severity.value,
        "status": i.status.value,
        "security": i.security,
        "services": list(i.services),
        "cells": list(i.cells),
        "roles": i.roles,
        "declared_by": i.declared_by,
        "declared_at": _iso(i.declared_at),
        "mitigated_at": _iso(i.mitigated_at),
        "resolved_at": _iso(i.resolved_at),
        "closed_at": _iso(i.closed_at),
        "pir_ref": i.pir_ref,
        "version": i.version,
    }
    if log is not None:
        out["timeline"] = [
            {
                "seq": e["seq"],
                "kind": e["kind"],
                "actor": str(e["actor"]),
                "note": e["note"],
                "data": e["data"],
                "at": _iso(e["at"]),
            }
            for e in log
        ]
    if items is not None:
        out["action_items"] = [
            {
                "item_id": iid,
                "title": a.title,
                "owner": a.owner,
                "due": _iso(a.due),
                "verified_by": a.verified_by,
            }
            for iid, a in items
        ]
    return out


async def _run(request: Request, op: Any, fn: Any) -> Any:
    actor, ctx = _actor(request, op)
    try:
        async with tenant_transaction(request_engine(request), ctx) as conn:
            return await fn(conn, actor)
    except OperationsError as e:
        raise http(e) from e


# ------------------------------------------------------------------------------------ bodies


class FreezeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{2,63}$")
    incident_id: uuid.UUID | None = None


class DeclareBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=4, max_length=200)
    severity: Severity
    security: bool = False
    services: list[Annotated[str, Field(pattern=r"^[a-z][a-z0-9-]{1,62}$")]] = Field(
        default_factory=list, max_length=20
    )
    cells: list[Annotated[str, Field(pattern=r"^[a-z0-9-]{1,40}$")]] = Field(
        default_factory=list, max_length=20
    )
    roles: dict[str, uuid.UUID] = Field(default_factory=dict)


class UpdateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    severity: Severity | None = None
    status: Literal["MITIGATING", "RECOVERY_VALIDATION"] | None = None
    roles: dict[str, uuid.UUID] | None = None
    reason: str = Field(min_length=4, max_length=2000)


class EntryBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["ACTION", "DECISION", "COMMUNICATION"]
    note: str = Field(min_length=2, max_length=4000)
    data: dict[str, str | int | bool] = Field(default_factory=dict, max_length=20)


class ResolveBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    validation: str = Field(min_length=10, max_length=4000)


class ItemBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=4, max_length=300)
    owner: uuid.UUID | None = None
    due: date | None = None


class CloseBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pir_ref: str | None = Field(default=None, min_length=2, max_length=256)


class GateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    gate: str = Field(max_length=40)
    status: GateStatus
    evidence_ref: str | None = Field(default=None, max_length=256)
    risk_owner: uuid.UUID | None = None
    risk_expires: date | None = None
    compensating_control: str | None = Field(default=None, max_length=1000)


class ReviewBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    service_id: str = Field(pattern=r"^[a-z][a-z0-9-]{1,62}$")
    release_ref: str = Field(min_length=2, max_length=256)
    gates: list[GateBody] = Field(max_length=20)


# ---------------------------------------------------------------------------------- services


def _slos(request: Request, svc: Service) -> list[dict[str, Any]]:
    slis = getattr(request.app.state, "journey_slis", None)
    windows = {"availability": slis.availability, "latency": slis.latency} if slis else {}
    return service.slo_view(svc, windows)


@router.get("/services")
async def list_services(request: Request, op: OperatorDep) -> dict[str, Any]:
    cat = _cat(request)
    return {"version": cat.version, "services": [s.view() for s in cat.services.values()]}


@router.get("/services/{service_id}/slos")
async def get_slos(service_id: str, request: Request, op: OperatorDep) -> dict[str, Any]:
    svc = _svc(request, service_id)
    return {
        "service_id": service_id,
        "tier": svc.tier,
        "scope": "process",
        "slos": _slos(request, svc),
    }


@router.get("/services/{service_id}/change-policy")
async def change_policy(
    service_id: str,
    request: Request,
    op: OperatorDep,
    change_class: ChangeClass = ChangeClass.STANDARD,
) -> dict[str, Any]:
    svc = _svc(request, service_id)
    slos = _slos(request, svc)
    frozen = await _run(request, op, lambda conn, _: store.active_freeze(conn, service_id))
    d = service.policy(slos, change_class, frozen is not None)
    return {
        "service_id": service_id,
        "change_class": change_class.value,
        "budget_state": service.worst_state(slos).value,
        "frozen": frozen is not None,
        "allowed": d.allowed,
        "requires": d.requires,
        "reason": d.reason,
    }


@router.get("/services/{service_id}/health")
async def service_health(service_id: str, request: Request, op: OperatorDep) -> dict[str, Any]:
    svc = _svc(request, service_id)
    slos = _slos(request, svc)
    page, ticket = service.paging(slos)

    async def read(conn: Any, _: Actor) -> tuple[Any, list[Incident]]:
        return await store.active_freeze(conn, service_id), await store.incidents(
            conn, open_only=True
        )

    frozen, open_incidents = await _run(request, op, read)
    mine = [i for i in open_incidents if service_id in i.services]
    worst = min(mine, key=lambda i: list(Severity).index(i.severity)) if mine else None
    avail = next((s for s in slos if s["sli"] == "availability" and s.get("events")), None)
    state = operational_state(
        ready=True,  # this process answers; per-dependency readiness is /health/ready
        paging=page,
        ticketing=ticket,
        availability_short=avail["good_ratio"] if avail else None,
        frozen=frozen is not None,
        open_incident=(worst.severity, worst.status, worst.security) if worst else None,
        failover_in_progress=False,
    )
    return {
        "service_id": service_id,
        "tier": svc.tier,
        "state": state.value,
        "frozen": frozen is not None,
        "open_incidents": [i.incident_id for i in mine],
        "slos": slos,
    }


@router.get("/services/{service_id}/readiness")
async def service_readiness(service_id: str, request: Request, op: OperatorDep) -> dict[str, Any]:
    _svc(request, service_id)
    result: dict[str, Any] = await _run(
        request, op, lambda conn, _: service.readiness(conn, service_id, datetime.now(UTC).date())
    )
    return result


@router.post("/services/{service_id}:freeze-changes", status_code=201)
async def freeze(
    service_id: str, body: FreezeBody, request: Request, op: OperatorDep
) -> dict[str, Any]:
    cat = _cat(request)
    fid = await _run(
        request,
        op,
        lambda conn, a: service.freeze(
            conn,
            cat,
            a,
            service_id,
            reason=body.reason_code,
            incident_id=str(body.incident_id) if body.incident_id else None,
            now=datetime.now(UTC),
        ),
    )
    return {"service_id": service_id, "freeze_id": fid, "frozen": True}


@router.post("/services/{service_id}:unfreeze-changes")
async def unfreeze(service_id: str, request: Request, op: OperatorDep) -> dict[str, Any]:
    _svc(request, service_id)
    fid = await _run(
        request, op, lambda conn, a: service.unfreeze(conn, a, service_id, now=datetime.now(UTC))
    )
    return {"service_id": service_id, "freeze_id": fid, "frozen": False}


@router.get("/cells/{cell_id}/health")
async def cell_health(cell_id: str, request: Request, op: OperatorDep) -> dict[str, Any]:
    if not _CELL.fullmatch(cell_id):
        raise ZidError(
            "CELL_ID_INVALID",
            "A cell id is lowercase letters, digits and hyphens.",
            http_status=422,
        )

    async def read(conn: Any, _: Actor) -> tuple[dict[str, Any], bool]:
        return await cell_view(conn, cell_id), await operation_in_progress(conn, cell_id)

    view, moving = await _run(request, op, read)
    status = view.get("status", "ACTIVE")
    state = (
        "FAILOVER_IN_PROGRESS"
        if moving
        else "MAJOR_OUTAGE"
        if status == "QUARANTINED"
        else "DEGRADED"
        if status in ("DEGRADED", "DRAINING")
        else "UNKNOWN"
        if view.get("reported") is False
        else "HEALTHY"
    )
    return {**view, "state": state, "recovery_in_progress": moving}


# --------------------------------------------------------------------------------- incidents


@router.post("/incidents", status_code=201)
async def declare(body: DeclareBody, request: Request, op: OperatorDep) -> dict[str, Any]:
    cat = _cat(request)
    i = await _run(
        request,
        op,
        lambda conn, a: service.declare(
            conn,
            cat,
            a,
            title=body.title,
            severity=body.severity,
            security=body.security,
            services=body.services,
            cells=body.cells,
            roles={k: str(v) for k, v in body.roles.items()},
            now=datetime.now(UTC),
        ),
    )
    return inc_view(i)


@router.get("/incidents")
async def list_incidents(
    request: Request, op: OperatorDep, open: Annotated[bool, Query()] = False
) -> dict[str, Any]:
    rows = await _run(request, op, lambda conn, _: store.incidents(conn, open_only=open))
    return {"incidents": [inc_view(i) for i in rows]}


@router.get("/incidents/{incident_id}")
async def get_incident(incident_id: uuid.UUID, request: Request, op: OperatorDep) -> dict[str, Any]:
    async def read(conn: Any, _: Actor) -> Any:
        i = await store.load_incident(conn, str(incident_id))
        if i is None:
            raise OperationsError("INCIDENT_NOT_FOUND", "No such incident.")
        return (
            i,
            await store.timeline(conn, i.incident_id),
            await store.action_items(conn, i.incident_id),
        )

    i, log, items = await _run(request, op, read)
    return inc_view(i, log, items)


@router.patch("/incidents/{incident_id}")
async def update_incident(
    incident_id: uuid.UUID, body: UpdateBody, request: Request, op: OperatorDep
) -> dict[str, Any]:
    i = await _run(
        request,
        op,
        lambda conn, a: service.update(
            conn,
            a,
            str(incident_id),
            severity=body.severity,
            status=IncidentStatus(body.status) if body.status else None,
            roles={k: str(v) for k, v in body.roles.items()} if body.roles else None,
            reason=body.reason,
            now=datetime.now(UTC),
        ),
    )
    return inc_view(i)


@router.post("/incidents/{incident_id}/actions", status_code=201)
async def add_entry(
    incident_id: uuid.UUID, body: EntryBody, request: Request, op: OperatorDep
) -> dict[str, Any]:
    seq = await _run(
        request,
        op,
        lambda conn, a: service.add_entry(
            conn, a, str(incident_id), kind=body.kind, note=body.note, data=body.data
        ),
    )
    return {"incident_id": str(incident_id), "seq": seq}


class EvidenceBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    artifacts: list[
        Literal["DECISION_RECEIPTS", "AUDIT_RECORDS", "LOGS", "FORENSIC_IMAGE", "CUSTODY_RECORD"]
    ] = Field(min_length=1, max_length=5)
    custody_ref: str = Field(min_length=8, max_length=128)
    note: str = Field(min_length=4, max_length=2000)


@router.post("/incidents/{incident_id}/evidence", status_code=201)
async def preserve_evidence(
    incident_id: uuid.UUID, body: EvidenceBody, request: Request, op: OperatorDep
) -> dict[str, Any]:
    """Record the decision receipts and other artifacts kept, and the chain-of-custody record covering them. A
    security or major incident cannot leave mitigation, or resolve, until this is on its timeline (OPS-20)."""
    seq = await _run(
        request,
        op,
        lambda conn, a: service.preserve_evidence(
            conn,
            a,
            str(incident_id),
            artifacts=body.artifacts,
            custody_ref=body.custody_ref,
            note=body.note,
            now=datetime.now(UTC),
        ),
    )
    return {"incident_id": str(incident_id), "seq": seq, "artifacts": sorted(body.artifacts)}


@router.post("/incidents/{incident_id}:resolve")
async def resolve(
    incident_id: uuid.UUID, body: ResolveBody, request: Request, op: OperatorDep
) -> dict[str, Any]:
    i = await _run(
        request,
        op,
        lambda conn, a: service.resolve(
            conn, a, str(incident_id), validation=body.validation, now=datetime.now(UTC)
        ),
    )
    return inc_view(i)


@router.post("/incidents/{incident_id}/action-items", status_code=201)
async def add_item(
    incident_id: uuid.UUID, body: ItemBody, request: Request, op: OperatorDep
) -> dict[str, Any]:
    iid = await _run(
        request,
        op,
        lambda conn, a: service.add_action_item(
            conn,
            a,
            str(incident_id),
            title=body.title,
            owner=str(body.owner) if body.owner else None,
            due=body.due,
        ),
    )
    return {"incident_id": str(incident_id), "item_id": iid}


@router.post("/incidents/{incident_id}/action-items/{item_id}:verify")
async def verify_item(
    incident_id: uuid.UUID, item_id: uuid.UUID, request: Request, op: OperatorDep
) -> dict[str, Any]:
    await _run(
        request,
        op,
        lambda conn, a: service.verify_action_item(conn, a, str(incident_id), str(item_id)),
    )
    return {"incident_id": str(incident_id), "item_id": str(item_id), "verified": True}


@router.post("/incidents/{incident_id}:close")
async def close(
    incident_id: uuid.UUID, body: CloseBody, request: Request, op: OperatorDep
) -> dict[str, Any]:
    i = await _run(
        request,
        op,
        lambda conn, a: service.close(
            conn, a, str(incident_id), pir_ref=body.pir_ref, now=datetime.now(UTC)
        ),
    )
    return inc_view(i)


@router.post("/readiness-reviews", status_code=201)
async def record_review(body: ReviewBody, request: Request, op: OperatorDep) -> dict[str, Any]:
    cat = _cat(request)
    gates = [
        GateResult(
            g.gate,
            g.status,
            g.evidence_ref,
            str(g.risk_owner) if g.risk_owner else None,
            g.risk_expires,
            g.compensating_control,
        )
        for g in body.gates
    ]
    result: dict[str, Any] = await _run(
        request,
        op,
        lambda conn, a: service.record_review(
            conn,
            cat,
            a,
            service_id=body.service_id,
            release_ref=body.release_ref,
            gates=gates,
            now=datetime.now(UTC),
        ),
    )
    return result
