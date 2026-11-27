"""Outside identity provider bindings, as evidence (ZID-CP-WF07 v2.0 .03; CR-169).

    POST /v1/machine/agents/{id}/idp-bindings      allowlist one provider for one agent: issuer, audience, the provider's subject and its pinned PUBLIC keys (machine.agent.manage)
    GET  /v1/machine/idp-bindings[?agent_id=]      the bindings and the profiles                                                                           (machine.agent.read)
    GET  /v1/machine/idp-bindings/{id}             one binding
    POST /v1/machine/idp-bindings/{id}:present     check a token from the provider against what was pinned; a failed check is recorded and answered     (machine.agent.manage)
    POST /v1/machine/idp-bindings/{id}:revoke                                                                                                         (machine.agent.manage)

A binding is evidence. It never activates an agent, asserts a corporate authority or creates a mandate, and every answer says so. Okta and Entra are claim-name profiles, not certified connectors.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from zid_db import tenant_transaction
from zid_errors import ZidError

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.modules.machine.application import idp as svc
from zid_platform.modules.machine.domain.agents import MachineError
from zid_platform.protected import protected_action

router = APIRouter(prefix="/v1/machine", tags=["agent-identity-bindings"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]


class IdpBindingBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile: str = Field(max_length=20)
    issuer: str = Field(max_length=300)
    subject: str = Field(max_length=200)
    audience: str = Field(max_length=300)
    keys: list[dict[str, Any]] = Field(max_length=4)
    algorithms: list[str] = Field(default_factory=list, max_length=4)
    environment: str = Field(default="PRODUCTION", max_length=10)
    expires_at: datetime | None = None


class IdpPresentBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(min_length=20, max_length=8192)


def http(e: MachineError) -> ZidError:
    code = e.code
    status = (
        404
        if code.endswith("NOT_FOUND")
        else 422
        if code.endswith("_INVALID") and code != "IDP_BINDING_STATE_INVALID"
        else 409
    )
    return ZidError(code, str(e), http_status=status)


async def _run(request: Request, caller: Caller, action: str, fn: Any) -> Any:
    try:
        async with tenant_transaction(request_engine(request), caller.ctx) as conn:
            await protected_action(conn, caller, action=action, resource="agent:*")
            return await fn(conn)
    except MachineError as e:
        raise http(e) from e


@router.post("/agents/{agent_id}/idp-bindings", status_code=201)
async def create_binding(
    agent_id: str, body: IdpBindingBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    tenant, actor = caller.ctx.tenant_id or "", caller.ctx.principal_id or ""
    return await _run(  # type: ignore[no-any-return]
        request, caller, "machine.agent.manage",
        lambda conn: svc.create(
            conn, tenant, actor, agent_id, profile=body.profile, issuer=body.issuer, subject=body.subject, audience=body.audience, keys=body.keys, algorithms=body.algorithms, environment=body.environment,
            expires_at=body.expires_at,
        ),
    )  # fmt: skip


@router.get("/idp-bindings")
async def list_bindings(
    request: Request,
    caller: CallerDep,
    agent_id: str | None = Query(default=None, min_length=36, max_length=36),
) -> dict[str, Any]:
    return await _run(  # type: ignore[no-any-return]
        request, caller, "machine.agent.read", lambda conn: svc.list_for(conn, agent_id)
    )


@router.get("/idp-bindings/{binding_id}")
async def read_binding(binding_id: str, request: Request, caller: CallerDep) -> dict[str, Any]:
    return await _run(  # type: ignore[no-any-return]
        request, caller, "machine.agent.read", lambda conn: svc.read(conn, binding_id)
    )


@router.post("/idp-bindings/{binding_id}:present")
async def present_token(
    binding_id: str, body: IdpPresentBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    tenant, actor = caller.ctx.tenant_id or "", caller.ctx.principal_id or ""
    return await _run(  # type: ignore[no-any-return]
        request,
        caller,
        "machine.agent.manage",
        lambda conn: svc.present(conn, tenant, actor, binding_id, body.token),
    )


@router.post("/idp-bindings/{binding_id}:revoke")
async def revoke_binding(binding_id: str, request: Request, caller: CallerDep) -> dict[str, Any]:
    tenant, actor = caller.ctx.tenant_id or "", caller.ctx.principal_id or ""
    return await _run(  # type: ignore[no-any-return]
        request,
        caller,
        "machine.agent.manage",
        lambda conn: svc.revoke(conn, tenant, actor, binding_id),
    )
