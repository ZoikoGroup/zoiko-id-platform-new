"""API-based agents over HTTP (CR-093): the same registry, with an API client in place of a container image.

    POST /v1/machine/api-clients                         register a client's PUBLIC keys as a build (DRAFT)    machine.build.register
    GET  /v1/machine/agents/{id}/api-binding             the binding, its limits and the key fingerprints      machine.agent.read
    POST /v1/machine/agents/{id}/api-binding             bind the approved client build to the agent           machine.agent.manage
    POST /v1/machine/agents/{id}/api-binding:revoke      revoke it (terminal); a running agent is suspended    machine.agent.manage
    POST /v1/machine/api-agent-token                     a signed one-time assertion for a short-lived token   PROTOCOL (the signature is the credential; CR-094)

Approval of the build, the blueprint release, activation and every other gate are the existing routes under /v1/machine. Nothing here accepts or
returns a private key: a key with secret members is refused by code, and the refusal names the problem, not the key.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from zid_context import RequestContext, current_context
from zid_db import tenant_transaction
from zid_errors import ZidError

from zid_platform import api_agents_switch
from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.modules.commercial.ports import key_m2m, record_billable
from zid_platform.modules.machine.api.agents import _actor, _run, _tenant
from zid_platform.modules.machine.application import api_agent_token
from zid_platform.modules.machine.application import api_agents as service
from zid_platform.modules.machine.domain import api_agents as domain
from zid_platform.protected import protected_action


def _require_api_agents() -> None:
    """The whole surface is a 404 while API agents are off, as a feature that is not there (CR-144)."""
    if not api_agents_switch.enabled():
        raise ZidError("API_AGENTS_DISABLED", "API-based agents are not enabled.", http_status=404)


router = APIRouter(
    prefix="/v1/machine", tags=["agent-registry"], dependencies=[Depends(_require_api_agents)]
)
CallerDep = Annotated[Caller, Depends(authenticated_caller)]


class ApiClientBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=2, max_length=120)
    jwks: dict[str, Any]  # the PUBLIC key set; a key with private members is refused
    audiences: list[str] = Field(min_length=1, max_length=5)
    source_ref: str | None = Field(default=None, max_length=300)
    provenance_ref: str | None = Field(default=None, max_length=300)


@router.post("/api-clients", status_code=201)
async def register_api_client(
    body: ApiClientBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    async def go(conn: Any) -> dict[str, Any]:
        await protected_action(conn, caller, action="machine.build.register", resource="build:new")
        return await service.register_api_client(
            conn, _tenant(caller), actor=_actor(caller), **body.model_dump()
        )

    return await _run(request, caller, go)  # type: ignore[no-any-return]


@router.get("/agents/{agent_id}/api-binding")
async def read_binding(agent_id: str, request: Request, caller: CallerDep) -> dict[str, Any]:
    async def go(conn: Any) -> dict[str, Any]:
        await protected_action(
            conn, caller, action="machine.agent.read", resource=f"agent:{agent_id}"
        )
        return await service.view(conn, agent_id)

    return await _run(request, caller, go)  # type: ignore[no-any-return]


@router.post("/agents/{agent_id}/api-binding", status_code=201)
async def bind(agent_id: str, request: Request, caller: CallerDep) -> dict[str, Any]:
    async def go(conn: Any) -> dict[str, Any]:
        await protected_action(
            conn, caller, action="machine.agent.manage", resource=f"agent:{agent_id}"
        )
        return await service.bind_api_client(conn, _tenant(caller), agent_id, actor=_actor(caller))

    return await _run(request, caller, go)  # type: ignore[no-any-return]


class ApiBindingRevokeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason_code: str = Field(min_length=2, max_length=64, pattern=r"^[A-Z0-9_]+$")


@router.post("/agents/{agent_id}/api-binding:revoke")
async def revoke(
    agent_id: str, body: ApiBindingRevokeBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    async def go(conn: Any) -> dict[str, Any]:
        await protected_action(
            conn, caller, action="machine.agent.manage", resource=f"agent:{agent_id}"
        )
        return await service.revoke_api_binding(
            conn, _tenant(caller), agent_id, reason=body.reason_code, actor=_actor(caller)
        )

    return await _run(request, caller, go)  # type: ignore[no-any-return]


class AssertionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    assertion: str = Field(min_length=20, max_length=4096)


def token_address(issuer: str) -> str:
    """The exact audience an assertion must name: this platform's own token address."""
    return issuer.rstrip("/") + "/v1/machine/api-agent-token"


@router.post("/api-agent-token")
async def api_agent_token_route(body: AssertionBody, request: Request) -> dict[str, Any]:
    """Exchange a signed one-time assertion for a short-lived token for the agent's principal. Nothing is read from a header the caller sets:
    the company and client come from the assertion only to choose where to look, and the token is minted only if the signature verifies against
    the PUBLIC keys stored with the approved build. The token is bound to the signing key (DPoP) and carries the gateway scope, so every later
    call is still governed (authority, mandate, policy, the tool cap)."""
    from zid_platform.modules.session.ports import issue_access_token

    settings = getattr(request.app.state, "token_settings", None)
    if settings is None:
        raise ZidError("UNAUTHENTICATED", "No token issuer is configured.", http_status=503)
    hint = api_agent_token.unverified_claims(body.assertion)
    try:
        tenant = str(uuid.UUID(str(hint.get("tenant_id"))))
    except ValueError:
        raise ZidError(
            api_agent_token.UNVERIFIED, "The assertion was not accepted.", http_status=401
        ) from None
    base = current_context() or RequestContext(
        "req_api_agent", "cor_api_agent", "zoiko-id", "unknown"
    )
    ctx = base.with_identity(tenant, None)
    now = datetime.now(UTC)
    async with tenant_transaction(request_engine(request), ctx) as conn:
        out = await api_agent_token.exchange(
            conn, tenant, body.assertion, token_audience=token_address(settings.issuer), now=now
        )
        if out["ok"]:
            await record_billable(
                conn,
                tenant_id=tenant,
                meter="M2M_GRANT",
                key=key_m2m(f"api-agent:{uuid.uuid4()}"),
                at=now,
                principal_class="NON_HUMAN",
            )
    if not out[
        "ok"
    ]:  # raised after the transaction so the burned id and the refusal evidence are kept
        if out["verified"]:
            raise ZidError(out["codes"][0], "This agent may not act right now.", http_status=403)
        raise ZidError(
            api_agent_token.UNVERIFIED, "The assertion was not accepted.", http_status=401
        )
    token = issue_access_token(
        settings.keyring, issuer=settings.issuer, audience=settings.audience, subject=out["agent_id"], tenant_id=tenant,
        client_id=f"api-agent:{out['client_id']}", now=now, scope=frozenset({"workload"}), dpop_jkt=out["jkt"],
        risk_class=domain.API_TOKEN_RISK_CLASS,
        lifetime=out["expires_at"] - now,
    )  # fmt: skip
    return {
        "access_token": token,
        "token_type": "DPoP",
        "expires_in": int((out["expires_at"] - now).total_seconds()),
        "principal_id": out["agent_id"],
        "cnf": {"jkt": out["jkt"]},
        "attestation_cap": domain.API_ATTESTATION_CAP,
    }
