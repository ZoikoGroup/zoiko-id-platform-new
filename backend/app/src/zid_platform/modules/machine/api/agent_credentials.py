"""A signed credential for an agent, and a verifier (ZID-CP-WF07 v2.0 .09; D08; CR-169).

    POST /v1/machine/agents/{id}/credentials      issue one, bound to the agent's own PUBLIC key; the credential text comes back once in full  (machine.agent.manage)
    GET  /v1/machine/agent-credentials[?agent_id=] the credentials and their live status                                                       (machine.agent.read)
    POST /v1/machine/agent-credentials/{id}:revoke                                                                                            (machine.agent.manage)
    POST /v1/machine/agent-credentials:verify     a relying party in this company checks one: signature, live status of the credential AND the agent, and,
                                                  with a presentation signed by the agent's key for this relying party and nonce, possession of that key           (machine.agent.read)

The answer keeps identity, validity and key possession apart, and says that authority was not evaluated: it never says an agent may spend. The verifier belongs to the company that issued the credential. The signature can also
be checked offline with the platform's published keys, but an offline check proves neither live status nor who is holding the credential.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from zid_db import tenant_transaction
from zid_errors import ZidError

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.modules.machine.application import agent_credential as svc
from zid_platform.modules.machine.domain.agent_credential import CREDENTIAL_TYP
from zid_platform.modules.machine.domain.agents import MachineError
from zid_platform.protected import protected_action

router = APIRouter(prefix="/v1/machine", tags=["agent-credentials"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]


class AgentCredentialIssueBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    public_key: dict[str, Any]
    audience: str | None = Field(default=None, max_length=300)
    mandate_id: str | None = Field(default=None, min_length=36, max_length=36)
    evidence_class: str | None = Field(default=None, max_length=2)
    ttl_hours: int | None = Field(default=None, ge=1, le=720)


class AgentCredentialVerifyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    credential: str = Field(min_length=20, max_length=8192)
    presentation: str | None = Field(default=None, min_length=20, max_length=8192)
    relying_party: str = Field(min_length=3, max_length=300)
    nonce: str | None = Field(default=None, min_length=8, max_length=200)


def http(e: MachineError) -> ZidError:
    code = e.code
    status = 404 if code.endswith("NOT_FOUND") else 422 if code.endswith("_INVALID") and code != "AGENT_CREDENTIAL_STATE_INVALID" else 409
    return ZidError(code, str(e), http_status=status)


def _settings(request: Request) -> Any:
    settings = getattr(request.app.state, "token_settings", None)
    if settings is None:
        raise ZidError("UNAUTHENTICATED", "No token issuer is configured.", http_status=503)
    return settings


async def _run(request: Request, caller: Caller, action: str, fn: Any) -> Any:
    try:
        async with tenant_transaction(request_engine(request), caller.ctx) as conn:
            await protected_action(conn, caller, action=action, resource="agent:*")
            return await fn(conn)
    except MachineError as e:
        raise http(e) from e


@router.post("/agents/{agent_id}/credentials", status_code=201)
async def issue(agent_id: str, body: AgentCredentialIssueBody, request: Request, caller: CallerDep) -> dict[str, Any]:
    s = _settings(request)
    tenant, actor = caller.ctx.tenant_id or "", caller.ctx.principal_id or ""
    base = str(getattr(s, "issuer", "")).rstrip("/")
    return await _run(  # type: ignore[no-any-return]
        request, caller, "machine.agent.manage",
        lambda conn: svc.issue(
            conn, tenant, actor, agent_id, public_key=body.public_key, audience=body.audience, mandate_id=body.mandate_id, evidence_class=body.evidence_class, ttl_hours=body.ttl_hours,
            sign=lambda claims, typ: s.keyring.sign(claims, typ=typ), issuer=s.issuer, status_url=f"{base}/v1/machine/agent-credentials:verify",
        ),
    )  # fmt: skip


@router.get("/agent-credentials")
async def list_credentials(request: Request, caller: CallerDep, agent_id: str | None = Query(default=None, min_length=36, max_length=36)) -> dict[str, Any]:
    return await _run(request, caller, "machine.agent.read", lambda conn: svc.list_for(conn, agent_id))  # type: ignore[no-any-return]


@router.post("/agent-credentials/{credential_id}:revoke")
async def revoke(credential_id: str, request: Request, caller: CallerDep) -> dict[str, Any]:
    tenant, actor = caller.ctx.tenant_id or "", caller.ctx.principal_id or ""
    return await _run(request, caller, "machine.agent.manage", lambda conn: svc.revoke(conn, tenant, actor, credential_id))  # type: ignore[no-any-return]


@router.post("/agent-credentials:verify")
async def verify(body: AgentCredentialVerifyBody, request: Request, caller: CallerDep) -> dict[str, Any]:
    s = _settings(request)
    tenant, actor = caller.ctx.tenant_id or "", caller.ctx.principal_id or ""
    return await _run(  # type: ignore[no-any-return]
        request, caller, "machine.agent.read",
        lambda conn: svc.verify(
            conn, tenant, actor, credential=body.credential, presentation=body.presentation, relying_party=body.relying_party, nonce=body.nonce,
            verify_own=lambda token: s.keyring.verify(token, typ=CREDENTIAL_TYP)[1],
        ),
    )  # fmt: skip
