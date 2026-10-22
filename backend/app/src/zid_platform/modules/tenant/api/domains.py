"""Tenant domain verification API (ZID-OVERLAY-001 §5, §26; OVL-15; G-W2).

GET  /v1/tenant/domains                     the tenant's claims and their state   (OWNER/ADMIN)
POST /v1/tenant/domains                     start a DNS TXT / HTTPS challenge      tenant.domain.manage
POST /v1/tenant/domains/{id}:verify         check the published challenge          tenant.domain.manage
POST /v1/tenant/domains/{id}:revoke         withdraw a claim                       tenant.domain.manage
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from zid_db import tenant_transaction

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.modules.tenant.api.tenants import _principal, _require_role, http
from zid_platform.modules.tenant.application import domains as service
from zid_platform.modules.tenant.domain.domains import Method, instructions
from zid_platform.modules.tenant.domain.lifecycle import TenantError
from zid_platform.modules.tenant.infrastructure import domain_store as store
from zid_platform.modules.tenant.infrastructure.domain_store import Domain
from zid_platform.protected import protected_action

router = APIRouter(prefix="/v1/tenant/domains", tags=["tenant"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]
ACTION = "tenant.domain.manage"


def view(d: Domain, *, with_challenge: bool = False) -> dict[str, Any]:
    out: dict[str, Any] = {
        "domain_id": d.domain_id,
        "domain": d.domain,
        "method": d.method.value,
        "status": d.status.value,
        "expires_at": d.expires_at.isoformat(),
        "verified_at": d.verified_at.isoformat() if d.verified_at else None,
        "last_checked_at": d.last_checked_at.isoformat() if d.last_checked_at else None,
        "reverify_due_at": d.reverify_due_at.isoformat() if d.reverify_due_at else None,
    }
    if with_challenge:
        out["publish"] = instructions(d.domain, d.method, d.challenge_token)
    return out


def checks(request: Request) -> service.DomainChecks:
    c = getattr(request.app.state, "domain_checks", None)
    return c if c is not None else service.default_checks()


class DomainBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    domain: str = Field(min_length=3, max_length=253)
    method: Method = Method.DNS_TXT


@router.get("")
async def list_domains(request: Request, caller: CallerDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await _require_role(conn, caller)
        items = await store.list_domains(conn)
    return {"items": [view(d, with_challenge=d.status.value == "PENDING") for d in items]}


@router.post("", status_code=201)
async def start(body: DomainBody, request: Request, caller: CallerDep) -> dict[str, Any]:
    tenant = caller.ctx.tenant_id or ""
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(conn, caller, action=ACTION, resource=f"tenant:{tenant}")
        try:
            d = await service.start(
                conn, tenant, domain=body.domain, method=body.method, actor=_principal(caller)
            )
        except TenantError as e:
            raise http(e) from e
    return view(d, with_challenge=True)


@router.post("/{domain_id}:verify")
async def verify(domain_id: uuid.UUID, request: Request, caller: CallerDep) -> dict[str, Any]:
    tenant = caller.ctx.tenant_id or ""
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(conn, caller, action=ACTION, resource=f"tenant:{tenant}")
        try:
            d = await service.verify(
                conn, str(domain_id), checks=checks(request), actor=_principal(caller)
            )
        except TenantError as e:
            raise http(e) from e
    return view(d)


@router.post("/{domain_id}:revoke")
async def revoke(domain_id: uuid.UUID, request: Request, caller: CallerDep) -> dict[str, Any]:
    tenant = caller.ctx.tenant_id or ""
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(conn, caller, action=ACTION, resource=f"tenant:{tenant}")
        try:
            d = await service.revoke(conn, str(domain_id), actor=_principal(caller))
        except TenantError as e:
            raise http(e) from e
    return view(d)
