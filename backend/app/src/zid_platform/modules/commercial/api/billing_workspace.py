"""Customer billing workspace: the overview and the billing profile (ZID-UX-COM-008 B01, B04; slice L-14d, CR-054).

    GET  /v1/billing/overview            the state, the one next action, the payer and recent activity (billing.overview.read)
    GET  /v1/billing/profile             the payer's address, tax identifiers and billing contacts (billing.overview.read)
    GET  /v1/billing/profile/history     every saved version and which fields changed (billing.audit.read)
    PUT  /v1/billing/profile             replace it, based on the version read (billing.profile.manage)

The server decides what the page says; the legal payer and tax country are never edited here.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from zid_db import tenant_transaction
from zid_errors import ZidError

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.modules.commercial.application import billing_gate, billing_workspace
from zid_platform.modules.commercial.application.billing_workspace import ProfileError

router = APIRouter(tags=["commercial"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]
_STATUS = {
    "VERSION_CONFLICT": 409,
    "PAYER_CHANGE_REQUIRES_REVIEW": 409,
    "NO_CHANGE": 409,
    "ACCOUNT_REQUIRED": 409,
}


class ProfileBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=0)
    address: dict[str, str] = Field(max_length=8)
    tax_ids: list[dict[str, str]] = Field(max_length=5)
    contacts: dict[str, str] = Field(max_length=4)


@router.get(
    "/v1/billing/overview", dependencies=[Depends(billing_gate.permission("billing.overview.read"))]
)
async def billing_overview(request: Request, caller: CallerDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        return await billing_workspace.overview(conn, caller.ctx.tenant_id or "")


@router.get(
    "/v1/billing/profile", dependencies=[Depends(billing_gate.permission("billing.overview.read"))]
)
async def billing_profile(request: Request, caller: CallerDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        return await billing_workspace.get_profile(conn, caller.ctx.tenant_id or "")


@router.get(
    "/v1/billing/profile/history",
    dependencies=[Depends(billing_gate.permission("billing.audit.read"))],
)
async def billing_profile_history(request: Request, caller: CallerDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        return {"items": await billing_workspace.history(conn)}


@router.put(
    "/v1/billing/profile", dependencies=[Depends(billing_gate.permission("billing.profile.manage"))]
)
async def save_billing_profile(
    body: ProfileBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    if not caller.ctx.principal_id:
        raise ZidError(
            "UNAUTHENTICATED", "An authenticated principal is required.", http_status=401
        )
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        try:
            return await billing_workspace.save_profile(
                conn, caller.ctx.tenant_id or "", by=caller.ctx.principal_id, expected_version=body.expected_version,
                address=body.address, tax_ids=body.tax_ids, contacts=body.contacts,
            )  # fmt: skip
        except ProfileError as e:
            raise ZidError(e.code, e.detail, http_status=_STATUS.get(e.code, 422)) from e
