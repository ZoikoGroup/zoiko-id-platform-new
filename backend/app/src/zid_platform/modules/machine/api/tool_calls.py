"""The agent-to-tool gateway over HTTP (ZID-GAP-001 WP-11; MACHINE-001 §14-15).

    POST /v1/machine/tool-calls:preflight     the same question as authorize, answered by a rehearsal that records and holds nothing (CR-169)
    POST /v1/machine/tool-calls:authorize     an agent asks to call a tool; the answer is a short-lived grant or a refusal
    POST /v1/machine/tool-grants:redeem       the tool's server spends the grant once, just before it acts

The caller is the agent itself: its workload token's subject is the agent principal and its scope is `workload`. The agent
named in the request is never taken from the body, so a person's token (or any other principal's) cannot ask for a
grant on an agent's behalf. A refusal is a 403 that names every reason code; an approval-required refusal also names the
exact context a human approval must be bound to. Whatever the outcome, the call is on the audit trail with its chain.

Identity, not authority: a grant says the policy decision point allowed this one call, for this tool's own server, for
about two minutes. The tool still validates it (audience, expiry, nonce, argument digest) before it acts.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from zid_db import tenant_transaction
from zid_errors import ErrorDetail, ZidError

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.modules.decision.ports import AccessQuery, evaluate_access
from zid_platform.modules.evidence.ports import sign_evidence
from zid_platform.modules.machine.application import authority_history, preflight
from zid_platform.modules.machine.application import tool_gateway as gateway
from zid_platform.modules.machine.domain.agents import MachineError
from zid_platform.modules.machine.domain.tool_gateway import GRANT_TYP
from zid_platform.protected import catalog

router = APIRouter(prefix="/v1/machine", tags=["agent-tool-gateway"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]
SHA256 = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class ToolCallValue(BaseModel):
    model_config = ConfigDict(extra="forbid")
    amount: str = Field(min_length=1, max_length=40)  # a decimal string, never a float
    currency: str = Field(pattern=r"^[A-Z]{3}$")


class ToolCallBody(BaseModel):
    """No tenant and no agent here: both come from the authenticated caller."""

    model_config = ConfigDict(extra="forbid")
    tool_id: str = Field(min_length=3, max_length=128)
    resource_type: str = Field(min_length=1, max_length=64)
    resource_id: str = Field(min_length=1, max_length=256)
    arguments_digest: str = (
        SHA256  # the caller's digest of the exact arguments; the grant is bound to it
    )
    represented_principal_id: str | None = Field(default=None, min_length=36, max_length=36)
    purpose: str | None = Field(default=None, max_length=128)
    mandate_id: str | None = Field(default=None, min_length=36, max_length=36)
    approval_request_id: str | None = Field(default=None, min_length=36, max_length=36)
    reservation_id: str | None = Field(
        default=None, min_length=36, max_length=36
    )  # capacity this call will spend (CR-169)
    value: ToolCallValue | None = None


def _http(e: MachineError) -> ZidError:
    status = 404 if e.code.endswith("NOT_FOUND") else 422 if e.code.endswith("_INVALID") else 409
    return ZidError(e.code, str(e), http_status=status)


@router.post("/tool-calls:authorize")
async def authorize_tool_call(
    body: ToolCallBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    if "workload" not in caller.scope:
        raise ZidError(
            "TOOL_CALL_FORBIDDEN",
            "Only an agent's own workload credential may ask the tool gateway.",
            http_status=403,
        )
    settings = getattr(request.app.state, "token_settings", None)
    if settings is None:
        raise ZidError("UNAUTHENTICATED", "No token issuer is configured.", http_status=503)
    engine = request_engine(request)
    agent_id = caller.ctx.principal_id or ""
    tenant = caller.ctx.tenant_id or ""

    async def decide(q: AccessQuery) -> dict[str, Any]:
        return await evaluate_access(engine, caller, q, catalog=catalog())

    call = gateway.ToolCall(
        agent_id=agent_id,
        tool_id=body.tool_id,
        resource_type=body.resource_type,
        resource_id=body.resource_id,
        arguments_digest=body.arguments_digest,
        represented_principal_id=body.represented_principal_id,
        purpose=body.purpose,
        mandate_id=body.mandate_id,
        approval_request_id=body.approval_request_id,
        reservation_id=body.reservation_id,
        value=(body.value.amount, body.value.currency) if body.value else None,
    )
    try:
        async with tenant_transaction(engine, caller.ctx) as conn:
            result = await gateway.authorize_tool_call(
                conn,
                tenant,
                call,
                decide=decide,
                sign=lambda claims, typ: settings.keyring.sign(claims, typ=typ),
                issuer=settings.issuer,
                action_catalogued=lambda action: action in catalog(),
                receipt_sign=sign_evidence,
            )
            if result.allowed:
                # the authority as it stood for this decision, in the same transaction: no grant without its history (fails closed)
                await authority_history.record_for_allowed_call(
                    conn,
                    tenant,
                    decision_id=result.decision_id,  # type: ignore[arg-type]
                    agent_id=agent_id,
                    agent_chain=list(result.chain),
                    mandate_id=body.mandate_id,
                    at=datetime.now(UTC),
                )
    except MachineError as e:
        raise _http(e) from e
    if not result.allowed:
        details = [ErrorDetail(issue=code) for code in result.codes]
        if result.approval_context:
            details.append(ErrorDetail(field="approval_context", issue=result.approval_context))
        raise ZidError(
            "TOOL_CALL_DENIED",
            "This tool call is not allowed.",
            http_status=403,
            details=details,
        )
    assert result.grant is not None and result.expires_at is not None
    return {
        "grant": result.grant,
        "token_type": "action-grant",
        "expires_at": result.expires_at.isoformat(),
        "decision_id": result.decision_id,
        "agent_chain": list(result.chain),
    }


@router.post("/tool-calls:preflight")
async def preflight_tool_call(
    body: ToolCallBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    """Would this call be allowed right now? The real gateway runs and its transaction is thrown away, so nothing is granted, recorded, reserved or metered. ALLOW, DENY, APPROVAL_REQUIRED or UNAVAILABLE, with the reasons."""
    if "workload" not in caller.scope:
        raise ZidError(
            "TOOL_CALL_FORBIDDEN",
            "Only an agent's own workload credential may ask the tool gateway.",
            http_status=403,
        )
    settings = getattr(request.app.state, "token_settings", None)
    if settings is None:
        raise ZidError("UNAUTHENTICATED", "No token issuer is configured.", http_status=503)
    engine = request_engine(request)

    async def decide(q: AccessQuery) -> dict[str, Any]:
        return await evaluate_access(engine, caller, q, catalog=catalog(), simulate=True)

    call = gateway.ToolCall(
        agent_id=caller.ctx.principal_id or "", tool_id=body.tool_id, resource_type=body.resource_type, resource_id=body.resource_id, arguments_digest=body.arguments_digest,
        represented_principal_id=body.represented_principal_id, purpose=body.purpose, mandate_id=body.mandate_id, approval_request_id=body.approval_request_id, reservation_id=body.reservation_id,
        value=(body.value.amount, body.value.currency) if body.value else None,
    )  # fmt: skip
    try:
        async with tenant_transaction(engine, caller.ctx) as conn:
            await preflight.rehearse(
                conn, caller.ctx.tenant_id or "", call, decide=decide, sign=lambda claims, typ: settings.keyring.sign(claims, typ=typ), issuer=settings.issuer,
                action_catalogued=lambda action: action in catalog(),
            )  # fmt: skip
    except preflight.Rollback as r:
        return r.view
    raise ZidError(
        "PREFLIGHT_FAILED", "The rehearsal did not finish.", http_status=500
    )  # unreachable: rehearse always ends by raising


class RedeemBody(BaseModel):
    """The server says which arguments it is about to run; the grant must have been issued for exactly those."""

    model_config = ConfigDict(extra="forbid")
    grant: str = Field(min_length=20, max_length=8192)
    arguments_digest: str = SHA256


@router.post("/tool-grants:redeem")
async def redeem_tool_grant(
    body: RedeemBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    """The caller is the tool's own server: the grant's audience must be the caller's principal id, so a grant that
    leaks to another service is worthless there. The chain is checked again now, and the grant is marked used."""
    settings = getattr(request.app.state, "token_settings", None)
    if settings is None:
        raise ZidError("UNAUTHENTICATED", "No token issuer is configured.", http_status=503)
    engine = request_engine(request)
    try:
        async with tenant_transaction(engine, caller.ctx) as conn:
            result = await gateway.redeem_grant(
                conn,
                caller.ctx.tenant_id or "",
                grant=body.grant,
                server_id=caller.ctx.principal_id or "",
                arguments_digest=body.arguments_digest,
                verify=lambda token: settings.keyring.verify(token, typ=GRANT_TYP)[1],
            )
    except MachineError as e:
        status = (
            409
            if e.code == "TOOL_GRANT_USED"
            else 403
            if e.code.startswith("TOOL_GRANT_")
            else _http(e).http_status
        )
        raise ZidError(e.code, "This grant cannot be redeemed.", http_status=status) from e
    if not result.ok:
        status = 409 if result.code == "TOOL_GRANT_USED" else 403
        raise ZidError(
            result.code or "TOOL_GRANT_REFUSED",
            "This grant cannot be redeemed.",
            http_status=status,
        )
    assert result.call is not None
    return result.call
