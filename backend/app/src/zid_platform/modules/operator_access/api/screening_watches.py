"""Ongoing screening watches, operator plane (ZID-DTV-WF-06 S12, ENG-14; CR-113).

    POST /v1/screening/review/cases/{id}/watches        watch the subject of a case: classes and sources are that case's; created inactive      (screening.monitoring.manage)
    POST /v1/screening/review/watches/{id}:approve      one person's approval; the creator cannot approve and nobody approves twice               (screening.monitoring.manage)
    POST /v1/screening/review/watches/{id}:activate     switch it on: refused, naming every control, without two approvers and signed gates      (screening.monitoring.manage)
    GET  /v1/screening/review/watches/{id}              approvals, the last attempt and last success of each source, and whether it may say "monitored continuously"

Nothing runs by itself: the worker re-screens an active watch when its cadence has run out, and only where ongoing monitoring is approved for the market. "Monitored continuously" is read from successes, never
from attempts. Records carry ids and codes, never a name.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Request
from pydantic import BaseModel, ConfigDict, Field
from zid_db import in_tenant, tenant_transaction
from zid_errors import ZidError

from zid_platform.api_auth import request_engine
from zid_platform.modules.operator_access.api.screening_sources import sc
from zid_platform.modules.operator_access.ports import require_operator
from zid_platform.operator_actions import Actions

MANAGE = "screening.monitoring.manage"
IdPath = Annotated[uuid.UUID, Path()]

router = APIRouter(tags=["screening-watches"])
OperatorDep = Annotated[Any, Depends(require_operator)]

actions = Actions(
    namespace=uuid.UUID("e8b40c13-7d56-4a92-8f0b-31c5a9d27e64"),
    refused_event="screening.watch.refused",
    status={
        "CASE_NOT_FOUND": 404,
        "WATCH_NOT_FOUND": 404,
        "WATCH_HAS_NO_SOURCES": 409,
        "WATCH_INVALID": 422,
        "WATCH_NOT_ACTIVATABLE": 409,
        "APPROVER_CREATED_THE_WATCH": 403,
        "APPROVER_ALREADY_APPROVED": 409,
    },
    errors=lambda: (sc.ScreeningError,),
    refused_code="SCREENING_WATCH_REFUSED",
    capability_hint="the screening monitoring capability",
)


class WatchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cadence_days: int = Field(ge=1, le=365)
    triggers: list[str] = Field(
        default_factory=lambda: ["SCHEDULE", "LIST_UPDATE"], min_length=1, max_length=5
    )


def _gates_override(request: Request) -> bool:
    """A deployment's own test setup may declare the gates signed. Left alone it is False, and the real release gates decide."""
    return bool(getattr(request.app.state, "screening_gates_signed", False))


@router.post("/v1/screening/review/cases/{request_id}/watches", status_code=201)
async def create(
    request_id: IdPath, body: WatchIn, request: Request, op: OperatorDep
) -> dict[str, Any]:
    try:
        triggers = frozenset(sc.watch_trigger(t) for t in body.triggers)
    except ValueError as e:
        raise ZidError(
            "TRIGGER_UNKNOWN", "One of those triggers is not one a watch uses.", http_status=422
        ) from e

    async def work(conn: Any, now: datetime) -> tuple[dict[str, Any], str, dict[str, Any]]:
        tenant = await sc.screening_requests.locate(conn, str(request_id))
        if tenant is None:
            raise sc.ScreeningError("CASE_NOT_FOUND", str(request_id))
        async with in_tenant(conn, tenant):
            out = await sc.watches.create_watch(
                conn,
                str(request_id),
                created_by=str(op.principal_id),
                cadence=timedelta(days=body.cadence_days),
                triggers=triggers,
                now=now,
            )
        return (
            out,
            out["watch_id"],
            {
                "case": str(request_id),
                "cadence_days": body.cadence_days,
                "sources": len(out["sources"]),
            },
        )

    return await actions.act(
        request, op, event="screening.watch.created", action="create_watch", capability=MANAGE, target_type="screening_watch", target=str(request_id), work=work,
    )  # fmt: skip


async def _on_watch(
    request: Request, op: Any, watch_id: uuid.UUID, event: str, action: str, run: Any
) -> dict[str, Any]:
    async def work(conn: Any, now: datetime) -> tuple[dict[str, Any], str, dict[str, Any]]:
        tenant = await sc.watches.watch_tenant(conn, str(watch_id))
        if tenant is None:
            raise sc.ScreeningError("WATCH_NOT_FOUND", str(watch_id))
        async with in_tenant(conn, tenant):
            out = await run(conn)
        return out, str(watch_id), {k: v for k, v in out.items() if k in ("approvals", "active")}

    return await actions.act(
        request,
        op,
        event=event,
        action=action,
        capability=MANAGE,
        target_type="screening_watch",
        target=str(watch_id),
        work=work,
    )


@router.post("/v1/screening/review/watches/{watch_id}:approve")
async def approve(watch_id: IdPath, request: Request, op: OperatorDep) -> dict[str, Any]:
    return await _on_watch(
        request,
        op,
        watch_id,
        "screening.watch.approved",
        "approve_watch",
        lambda conn: sc.watches.approve_watch(conn, str(watch_id), approver=str(op.principal_id)),
    )


@router.post("/v1/screening/review/watches/{watch_id}:activate")
async def activate(watch_id: IdPath, request: Request, op: OperatorDep) -> dict[str, Any]:
    async def run(conn: Any) -> dict[str, Any]:
        signed = _gates_override(request)
        if not signed:
            signed = await sc.gates.is_go(
                conn,
                await sc.watches.market_of(conn, str(watch_id)),
                "MONITORING",
                now=datetime.now(UTC),
            )
        return await sc.watches.activate_watch(conn, str(watch_id), gates_signed=signed)

    return await _on_watch(
        request, op, watch_id, "screening.watch.activated", "activate_watch", run
    )


@router.get("/v1/screening/review/watches/{watch_id}")
async def one(watch_id: IdPath, request: Request, op: OperatorDep) -> dict[str, Any]:
    now = datetime.now(UTC)
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await actions.need(conn, op, MANAGE, f"regulatory:{watch_id}")
        tenant = await sc.watches.watch_tenant(conn, str(watch_id))
        if tenant is None:
            raise ZidError("WATCH_NOT_FOUND", "There is no such watch.", http_status=404)
        async with in_tenant(conn, tenant):
            return dict(await sc.watches.status(conn, str(watch_id), now=now))


__all__ = ["router"]
