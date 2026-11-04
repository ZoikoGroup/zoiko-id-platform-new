"""Billing access: what the caller may do in billing, and the Owner's grants (ZID-COM-CL-002; slice L-14, CR-050).

`check_permission` is the one decision every billing route asks (the commercial module is given it at start-up, because commercial
cannot import tenant). It answers from the Owner role or a live grant, read afresh each time, and refuses with a code the page can
act on. The grant routes are the Owner's only: a step-up, an audit record, no self-grant, and a grant never carries the right to grant.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from zid_db import tenant_transaction
from zid_errors import ErrorDetail, ZidError

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.modules.authentication.ports import require_step_up
from zid_platform.modules.tenant.application import billing_access as svc
from zid_platform.modules.tenant.domain.billing_access import (
    BUNDLES,
    PERMISSIONS,
    BillingAccessError,
)
from zid_platform.modules.tenant.infrastructure import billing_grants as store
from zid_platform.modules.tenant.infrastructure.role_matrix import billing_access_enforced

router = APIRouter(tags=["tenant"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]

_DELEGATION_STEP_UP = "grant-expand-transfer-or-revoke-high-impact-delegated-authority"


def _principal(caller: Caller) -> str:
    if not caller.ctx.principal_id:
        raise ZidError(
            "UNAUTHENTICATED", "An authenticated principal is required.", http_status=401
        )
    return caller.ctx.principal_id


async def check_permission(request: Request, caller: Caller, permission: str) -> None:
    """Decide in its own short transaction and commit the refusal record before raising, so a refused request leaves its proof.
    Under REPORT_ONLY (the owners' switch) a refusal is recorded and let through."""
    principal = _principal(caller)
    tenant = caller.ctx.tenant_id or ""
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        decision = await svc.decide_for(conn, tenant, principal, permission)
    if decision.allowed or not billing_access_enforced():
        return
    raise ZidError(
        "BILLING_PERMISSION_REQUIRED",
        "Your account does not have billing access for this.",
        http_status=403,
        details=[ErrorDetail(field="permission", issue=permission)],
    )


def _error(e: BillingAccessError) -> ZidError:
    status = {
        "BILLING_GRANT_NOT_FOUND": 404,
        "BILLING_DELEGATION_OWNER_ONLY": 403,
        "BILLING_GRANT_EXISTS": 409,
    }.get(e.code, 422)
    return ZidError(e.code, e.detail, http_status=status)


class BillingGrantBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    grantee_principal_id: uuid.UUID
    bundle: str = Field(min_length=1, max_length=32)
    reason: str = Field(min_length=1, max_length=500)
    expires_at: datetime | None = None


@router.get("/v1/tenant/billing-access/me")
async def my_billing_access(request: Request, caller: CallerDep) -> dict[str, Any]:
    """What this person can do in billing, so the page shows only the controls they can use. The server still decides each call."""
    principal = _principal(caller)
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        held = await svc.effective(conn, principal)
    return {
        "permissions": held,
        "catalogue": [{"key": k, "label": v} for k, v in PERMISSIONS.items()],
        "bundles": {b.value: sorted(keys) for b, keys in BUNDLES.items()},
        "enforced": billing_access_enforced(),
    }


@router.get("/v1/tenant/billing-grants")
async def billing_grants(request: Request, caller: CallerDep) -> dict[str, Any]:
    await check_permission(request, caller, "billing.audit.read")
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        return {"items": await store.list_grants(conn)}


@router.post("/v1/tenant/billing-grants", status_code=201)
async def give_billing_access(
    body: BillingGrantBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    principal, tenant = _principal(caller), caller.ctx.tenant_id or ""
    await check_permission(request, caller, "billing.access.delegate")
    error: BillingAccessError | None = None
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await require_step_up(
            request,
            conn,
            caller,
            action=_DELEGATION_STEP_UP,
            resource=f"tenant:{tenant}",
            change={
                "operation": "BILLING_GRANT",
                "grantee": str(body.grantee_principal_id),
                "bundle": body.bundle,
                "expires_at": body.expires_at.isoformat() if body.expires_at else None,
            },
        )
        try:
            grant_id = await svc.give(
                conn,
                tenant,
                principal,
                grantee_principal_id=str(body.grantee_principal_id),
                bundle=body.bundle,
                reason=body.reason,
                expires_at=body.expires_at,
            )
        except BillingAccessError as e:
            error = e  # the refusal record is already written; leave the block so it commits
    if error is not None:
        raise _error(error)
    return {"grant_id": grant_id}


@router.post("/v1/tenant/billing-grants/{grant_id}:revoke")
async def take_billing_access_away(
    grant_id: uuid.UUID, request: Request, caller: CallerDep
) -> dict[str, Any]:
    principal, tenant = _principal(caller), caller.ctx.tenant_id or ""
    await check_permission(request, caller, "billing.access.delegate")
    error: BillingAccessError | None = None
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await require_step_up(
            request,
            conn,
            caller,
            action=_DELEGATION_STEP_UP,
            resource=f"tenant:{tenant}",
            change={"operation": "BILLING_REVOKE", "grant_id": str(grant_id)},
        )
        try:
            await svc.take_away(conn, tenant, principal, str(grant_id))
        except BillingAccessError as e:
            error = e
    if error is not None:
        raise _error(error)
    return {"grant_id": str(grant_id), "revoked": True}
