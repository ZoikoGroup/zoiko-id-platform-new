"""Stopping agents by scope (ZID-CP-WF07 v2.0 .10; A07-08; CR-169).

    POST /v1/machine/revocations:plan             a dry run for any scope: who would be stopped, what would not be undone, where it is enforced   (agent.security.pause)
    POST /v1/machine/revocations                  stop an AGENT, a SPONSOR's agents, a BLUEPRINT's agents or the whole company; recorded with an epoch  (agent.security.pause)
    GET  /v1/machine/revocations[?open=true]      the stops                                                                                       (machine.agent.read)
    GET  /v1/machine/revocations/{id}             one stop
    POST /v1/machine/revocations/{id}:close       the original cause is closed, by someone other than the person who stopped          (agent.security.restore.close, R3)

A stop denies new calls. It does not undo a call that already ran, and it says which places it cannot reach. Agents it stopped are activated again through their own gates, which refuse until it is closed by someone else.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from zid_db import tenant_transaction
from zid_errors import ZidError

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.modules.machine.application import revocation as svc
from zid_platform.modules.machine.domain.agents import MachineError
from zid_platform.protected import protected_action

router = APIRouter(prefix="/v1/machine", tags=["agent-revocation"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]


class RevocationBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope: str = Field(max_length=10)
    subject: str | None = Field(default=None, max_length=200)


class RevocationStopBody(RevocationBody):
    reason: str = Field(max_length=300)


class RevocationCloseBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note: str = Field(max_length=300)


def http(e: MachineError) -> ZidError:
    code = e.code
    status = (
        404
        if code.endswith("NOT_FOUND")
        else 403
        if code in ("REVOCATION_CLOSE_NOT_INDEPENDENT", "REVOCATION_REINSTATE_NOT_INDEPENDENT")
        else 422
        if code.endswith("_INVALID")
        else 409
    )
    return ZidError(code, str(e), http_status=status)


async def _run(
    request: Request, caller: Caller, action: str, resource: str, fn: Any, *, change: str = ""
) -> Any:
    try:
        async with tenant_transaction(request_engine(request), caller.ctx) as conn:
            await protected_action(conn, caller, action=action, resource=resource, change=change)
            return await fn(conn)
    except MachineError as e:
        raise http(e) from e


@router.post("/revocations:plan")
async def plan(body: RevocationBody, request: Request, caller: CallerDep) -> dict[str, Any]:
    return await _run(  # type: ignore[no-any-return]
        request,
        caller,
        "agent.security.pause",
        "agent:*",
        lambda conn: svc.plan(conn, body.scope, body.subject),
    )


@router.post("/revocations", status_code=201)
async def stop(body: RevocationStopBody, request: Request, caller: CallerDep) -> dict[str, Any]:
    tenant, actor = caller.ctx.tenant_id or "", caller.ctx.principal_id or ""
    return await _run(  # type: ignore[no-any-return]
        request,
        caller,
        "agent.security.pause",
        "agent:*",
        lambda conn: svc.execute(conn, tenant, actor, body.scope, body.subject, body.reason),
    )


@router.get("/revocations")
async def list_stops(
    request: Request, caller: CallerDep, open: bool = Query(default=False)
) -> dict[str, Any]:
    return await _run(  # type: ignore[no-any-return]
        request,
        caller,
        "machine.agent.read",
        "agent:*",
        lambda conn: svc.list_events(conn, open_only=open),
    )


@router.get("/revocations/{event_id}")
async def read_stop(event_id: str, request: Request, caller: CallerDep) -> dict[str, Any]:
    return await _run(  # type: ignore[no-any-return]
        request,
        caller,
        "machine.agent.read",
        "agent:*",
        lambda conn: svc.read_event(conn, event_id),
    )


@router.post("/revocations/{event_id}:close")
async def close_stop(
    event_id: str, body: RevocationCloseBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    tenant, actor = caller.ctx.tenant_id or "", caller.ctx.principal_id or ""
    return await _run(  # type: ignore[no-any-return]
        request,
        caller,
        "agent.security.restore.close",
        f"agent:revocation:{event_id}",
        lambda conn: svc.close(conn, tenant, actor, event_id, body.note),
        change=f"close {event_id}",
    )
