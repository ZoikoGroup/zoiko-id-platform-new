"""Customer commitment approval and the Owner's delegations (ZID-COM-CL-004 section 04, section 11; slice L-14c, CR-054).

The customer's own authority approves a new or increased obligation. The server works out the exposure from the order, never from the caller, and decides who
may sign; the Owner alone gives or takes away a spend limit or a procurement role, each with a fresh strong authentication. A refusal that changed state
(a lapse) is kept: the error is raised after the transaction ends.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from zid_db import tenant_transaction
from zid_errors import ZidError

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.modules.authentication.ports import require_step_up
from zid_platform.modules.commercial.ports import CommitmentError, commitments
from zid_platform.modules.tenant.api.billing_access import check_permission

router = APIRouter(tags=["tenant"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]

_APPROVAL_STEP_UP = "execute-sensitive-approval-rejection"
_DELEGATION_STEP_UP = "grant-expand-transfer-or-revoke-high-impact-delegated-authority"
_STATUS = {
    "REQUEST_NOT_FOUND": 404, "ORDER_NOT_FOUND": 404, "DELEGATE_NOT_FOUND": 404,
    "NOT_AN_APPROVER": 403, "OVER_SPEND_LIMIT": 403, "SELF_APPROVAL": 403, "OWNER_ONLY": 403, "SELF_DELEGATION": 403,
    "NOT_THE_REQUESTER": 403, "POLICY_NOT_SIGNED": 403, "REVIEW_REQUIRED": 403,
    "CAP_INVALID": 422, "KIND_UNKNOWN": 422, "WINDOW_INVALID": 422, "DECISION_INVALID": 422, "REASON_REQUIRED": 422,
    "AMOUNT_INVALID": 422, "FX_UNPINNED": 422,
}  # fmt: skip


def _fail(e: CommitmentError) -> ZidError:
    return ZidError(e.code, e.detail, http_status=_STATUS.get(e.code, 409))


def _principal(caller: Caller) -> str:
    if not caller.ctx.principal_id:
        raise ZidError(
            "UNAUTHENTICATED", "An authenticated principal is required.", http_status=401
        )
    return caller.ctx.principal_id


def _env(request: Request) -> str:
    return str(getattr(request.app.state, "environment", "production"))


class RequestBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order_id: uuid.UUID


class DecisionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    reason: str | None = Field(default=None, max_length=500)


class DelegateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    principal_id: uuid.UUID
    kind: Literal["BILLING_SPEND", "PROCUREMENT_APPROVER"]
    cap_usd: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    effective_to: datetime | None = None


@router.get("/v1/billing/commitment-requests")
async def list_commitments(
    request: Request, caller: CallerDep, limit: Annotated[int, Query(ge=1, le=200)] = 100
) -> dict[str, Any]:
    await check_permission(request, caller, "billing.overview.read")
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        return {"items": await commitments.list_requests(conn, limit=limit)}


@router.post("/v1/billing/commitment-requests", status_code=201)
async def ask_for_approval(
    body: RequestBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    principal, tenant = _principal(caller), caller.ctx.tenant_id or ""
    await check_permission(request, caller, "billing.subscription.request_change")
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        try:
            return await commitments.request(
                conn, tenant_id=tenant, order_id=str(body.order_id), requested_by=principal
            )
        except CommitmentError as e:
            raise _fail(e) from e


@router.get("/v1/billing/commitment-requests/{request_id}")
async def get_commitment(
    request_id: uuid.UUID, request: Request, caller: CallerDep
) -> dict[str, Any]:
    await check_permission(request, caller, "billing.overview.read")
    refused: CommitmentError | None = None
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        try:
            return await commitments.get(conn, str(request_id))
        except CommitmentError as e:
            refused = e
    raise _fail(refused)


async def _decide(
    request: Request, caller: Caller, request_id: uuid.UUID, body: DecisionBody, decision: str
) -> dict[str, Any]:
    principal, tenant = _principal(caller), caller.ctx.tenant_id or ""
    refused: CommitmentError | None = None
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        proof = await require_step_up(
            request, conn, caller, action=_APPROVAL_STEP_UP, resource=f"tenant:{tenant}",
            change={"operation": f"COMMITMENT_{decision}", "request_id": str(request_id), "snapshot_hash": body.snapshot_hash},
        )  # fmt: skip
        try:
            return await commitments.decide(
                conn, tenant_id=tenant, request_id=str(request_id), approver=principal, decision=decision, presented_hash=body.snapshot_hash,
                reason=body.reason, step_up_at=datetime.now(UTC) if proof is not None else None, environment=_env(request),
            )  # fmt: skip
        except CommitmentError as e:
            refused = e
    raise _fail(refused)


@router.post("/v1/billing/commitment-requests/{request_id}:approve")
async def approve_commitment(
    request_id: uuid.UUID, body: DecisionBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    return await _decide(request, caller, request_id, body, "APPROVE")


@router.post("/v1/billing/commitment-requests/{request_id}:reject")
async def reject_commitment(
    request_id: uuid.UUID, body: DecisionBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    return await _decide(request, caller, request_id, body, "REJECT")


@router.post("/v1/billing/commitment-requests/{request_id}:cancel")
async def cancel_commitment(
    request_id: uuid.UUID, request: Request, caller: CallerDep
) -> dict[str, Any]:
    principal = _principal(caller)
    refused: CommitmentError | None = None
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        try:
            return await commitments.cancel(conn, request_id=str(request_id), by=principal)
        except CommitmentError as e:
            refused = e
    raise _fail(refused)


@router.get("/v1/billing/commitment-delegates")
async def list_delegates(request: Request, caller: CallerDep) -> dict[str, Any]:
    await check_permission(request, caller, "billing.audit.read")
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        return {"items": await commitments.delegates(conn)}


@router.post("/v1/billing/commitment-delegates", status_code=201)
async def give_delegation(
    body: DelegateBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    principal, tenant = _principal(caller), caller.ctx.tenant_id or ""
    await check_permission(request, caller, "billing.access.delegate")
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await require_step_up(
            request, conn, caller, action=_DELEGATION_STEP_UP, resource=f"tenant:{tenant}",
            change={"operation": "COMMITMENT_DELEGATE", "grantee": str(body.principal_id), "kind": body.kind, "cap_usd": str(body.cap_usd)},
        )  # fmt: skip
        try:
            return await commitments.delegate(
                conn, tenant_id=tenant, grantor=principal, principal=str(body.principal_id), kind=body.kind, cap_amount=body.cap_usd,
                effective_to=body.effective_to,
            )  # fmt: skip
        except CommitmentError as e:
            raise _fail(e) from e


@router.post("/v1/billing/commitment-delegates/{delegate_id}:revoke")
async def revoke_delegation(
    delegate_id: uuid.UUID, request: Request, caller: CallerDep
) -> dict[str, Any]:
    principal, tenant = _principal(caller), caller.ctx.tenant_id or ""
    await check_permission(request, caller, "billing.access.delegate")
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await require_step_up(
            request, conn, caller, action=_DELEGATION_STEP_UP, resource=f"tenant:{tenant}",
            change={"operation": "COMMITMENT_DELEGATE_REVOKE", "delegate_id": str(delegate_id)},
        )  # fmt: skip
        try:
            await commitments.revoke_delegate(
                conn, tenant_id=tenant, delegate_id=str(delegate_id), by=principal
            )
        except CommitmentError as e:
            raise _fail(e) from e
    return {"delegate_id": str(delegate_id), "revoked": True}
