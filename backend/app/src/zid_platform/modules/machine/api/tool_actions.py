"""What became of an authorized tool call, and its signed receipts (ZID-CP-WF07 v2.0 .07, .10, .11; CR-169).

    POST /v1/machine/tool-actions/{id}:finalize   the tool's own server reports EXECUTED, FAILED, UNKNOWN or COMPENSATED for an action it redeemed
    GET  /v1/machine/tool-actions                 people: the actions, newest change first          (machine.agent.read)
    GET  /v1/machine/tool-actions/{id}            one action with its latest receipt                (machine.agent.read)
    GET  /v1/machine/tool-actions/{id}/receipts   every receipt revision, each with whether it still verifies
    GET  /v1/machine/agents/{id}/stop-impact       a dry run of stopping an agent: who and what it would reach, and what it would not undo
    GET  /v1/machine/risk-signals                  places worth attention (never a score or a verdict)

The reporter is never named in the body: it is the authenticated caller, and it must be the server the grant was issued for. An outcome of UNKNOWN keeps any capacity held for the action held.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from zid_db import tenant_transaction
from zid_errors import ZidError

from zid_platform.api_auth import Caller, authenticated_caller, cell_writer_epoch, request_engine
from zid_platform.modules.evidence.ports import sign_evidence, verify_evidence
from zid_platform.modules.machine.application import tool_actions as svc
from zid_platform.modules.machine.domain.agents import MachineError
from zid_platform.modules.machine.domain.tool_action import RECEIPT_TYP
from zid_platform.modules.mandate.ports import settle_for_action
from zid_platform.protected import protected_action

router = APIRouter(prefix="/v1/machine", tags=["agent-tool-gateway"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]


class ToolActionFinalizeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    outcome: str = Field(min_length=6, max_length=12)
    downstream_ref: str | None = Field(default=None, max_length=128)


def http(e: MachineError) -> ZidError:
    code = e.code
    status = 404 if code.endswith("NOT_FOUND") else 422 if code.endswith("_INVALID") and code != "TOOL_ACTION_STATE_INVALID" else 409
    return ZidError(code, str(e), http_status=status)


def _settings(request: Request) -> Any:
    settings = getattr(request.app.state, "token_settings", None)
    if settings is None:
        raise ZidError("UNAUTHENTICATED", "No token issuer is configured.", http_status=503)
    return settings


@router.post("/tool-actions/{action_id}:finalize")
async def finalize_action(action_id: str, body: ToolActionFinalizeBody, request: Request, caller: CallerDep) -> dict[str, Any]:
    settings = _settings(request)
    tenant = caller.ctx.tenant_id or ""
    try:
        async with tenant_transaction(request_engine(request), caller.ctx) as conn:

            async def settle(effect: str, reservation_id: str) -> tuple[str, str | None]:
                cell, epoch = await cell_writer_epoch(request, conn, tenant)
                return await settle_for_action(
                    conn, tenant_id=tenant, reservation_id=reservation_id, effect=effect, cell_id=cell, writer_epoch=epoch, now=datetime.now(UTC)
                )

            return await svc.finalize(
                conn, tenant, server_id=caller.ctx.principal_id or "", action_id=action_id, outcome=body.outcome, downstream_ref=body.downstream_ref, settle=settle,
                sign=sign_evidence, issuer=settings.issuer,
            )  # fmt: skip
    except MachineError as e:
        raise http(e) from e


async def _read(request: Request, caller: Caller, fn: Any) -> Any:
    try:
        async with tenant_transaction(request_engine(request), caller.ctx) as conn:
            await protected_action(conn, caller, action="machine.agent.read", resource="agent:*")
            return await fn(conn)
    except MachineError as e:
        raise http(e) from e


@router.get("/tool-actions")
async def list_actions(
    request: Request, caller: CallerDep, state: str | None = Query(default=None, max_length=12), agent_id: str | None = Query(default=None, min_length=36, max_length=36)
) -> dict[str, Any]:
    return await _read(request, caller, lambda conn: svc.list_actions(conn, state=state, agent_id=agent_id))  # type: ignore[no-any-return]


@router.get("/tool-actions/{action_id}")
async def read_action(action_id: str, request: Request, caller: CallerDep) -> dict[str, Any]:
    verify = lambda token: verify_evidence(token, RECEIPT_TYP)  # noqa: E731
    return await _read(request, caller, lambda conn: svc.view(conn, action_id, verify))  # type: ignore[no-any-return]


@router.get("/tool-actions/{action_id}/receipts")
async def read_receipts(action_id: str, request: Request, caller: CallerDep) -> dict[str, Any]:
    verify = lambda token: verify_evidence(token, RECEIPT_TYP)  # noqa: E731
    return await _read(request, caller, lambda conn: svc.receipts(conn, action_id, verify))  # type: ignore[no-any-return]


@router.get("/agents/{agent_id}/stop-impact")
async def stop_impact(agent_id: str, request: Request, caller: CallerDep) -> dict[str, Any]:
    return await _read(request, caller, lambda conn: svc.stop_impact(conn, agent_id))  # type: ignore[no-any-return]


@router.get("/risk-signals")
async def risk_signals(request: Request, caller: CallerDep) -> dict[str, Any]:
    """Places worth a person's attention about the company's agents. Read only; never a score, never a decision."""
    from zid_platform.modules.machine.application import risk

    return await _read(request, caller, lambda conn: risk.signals(conn))  # type: ignore[no-any-return]
