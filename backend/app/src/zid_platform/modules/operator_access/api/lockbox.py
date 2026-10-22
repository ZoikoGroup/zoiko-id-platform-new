"""Customer side of operator access: lockbox approvals and the tenant's operator access policy
(DEC-F4 §2 "Customer lockbox"; TENANCY-001 §19; CR-012).

    GET  /v1/lockbox-requests?state=                     operator.lockbox.approve (R3/E3)
    POST /v1/lockbox-requests/{id}:approve               operator.lockbox.approve
    POST /v1/lockbox-requests/{id}:deny                  operator.lockbox.approve (PENDING: deny,
                                                         APPROVED: withdraw - access stops at once)
    GET  /v1/operator-access-policy                      operator.access_policy.manage (R3/E3)
    PUT  /v1/operator-access-policy                      operator.access_policy.manage

A customer tenant administrator, authorized by the PDP for the catalogued action (authority chain,
policy release, AAL and freshness for R3, security state) - never a role flag. Rows are
tenant-scoped under RLS: another tenant's lockbox request is simply not found.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from zid_db import tenant_transaction

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.modules.operator_access.api.operator import http
from zid_platform.modules.operator_access.application import service
from zid_platform.modules.operator_access.domain.model import LockboxState, OperatorAccessError
from zid_platform.modules.operator_access.infrastructure import store
from zid_platform.modules.operator_access.infrastructure.store import AccessPolicy, Lockbox
from zid_platform.protected import protected_action

router = APIRouter(tags=["operator-access"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]
LOCKBOX_ACTION = "operator.lockbox.approve"
POLICY_ACTION = "operator.access_policy.manage"


class DecisionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(default="", max_length=2000)


class PolicyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lockbox_required: bool = True
    allowed_operator_jurisdictions: list[str] = Field(default_factory=list, max_length=64)


def lockbox_view(lb: Lockbox, now: datetime) -> dict[str, Any]:
    return {
        "lockbox_request_id": lb.lockbox_request_id,
        "jit_grant_id": lb.jit_grant_id,
        "requester_principal_id": lb.requester_principal_id,
        "capability": lb.capability,
        "resource_scope": lb.resource_scope,
        "case_ref": lb.case_ref,
        "reason": lb.reason,
        "duration_minutes": int(lb.duration.total_seconds() // 60),
        "state": lb.effective_state(now).value,
        "created_at": lb.created_at.isoformat(),
        "expires_at": lb.expires_at.isoformat(),
        "decided_by": lb.decided_by,
        "decided_at": lb.decided_at.isoformat() if lb.decided_at else None,
    }


def policy_view(p: AccessPolicy) -> dict[str, Any]:
    return {
        "lockbox_required": p.lockbox_required,
        "allowed_operator_jurisdictions": list(p.allowed_operator_jurisdictions),
        "revision": p.revision,
    }


@router.get("/v1/lockbox-requests")
async def list_lockbox(
    request: Request,
    caller: CallerDep,
    state: LockboxState | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> dict[str, Any]:
    now = datetime.now(UTC)
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn, caller, action=LOCKBOX_ACTION, resource="lockbox_request:*", change="read"
        )
        stored = None if state in (None, LockboxState.EXPIRED) else state
        items = await store.list_lockbox(conn, state=stored.value if stored else None, limit=limit)
    views = [lockbox_view(lb, now) for lb in items]
    if state is not None:
        views = [v for v in views if v["state"] == state.value]
    return {"items": views}


async def _decide(
    request: Request, caller: Caller, lockbox_id: uuid.UUID, approve: bool, reason: str
) -> dict[str, Any]:
    now = datetime.now(UTC)
    try:
        async with tenant_transaction(request_engine(request), caller.ctx) as conn:
            await protected_action(
                conn,
                caller,
                action=LOCKBOX_ACTION,
                resource=f"lockbox_request:{lockbox_id}",
                change="approve" if approve else "deny",
            )
            lb = await service.decide_lockbox(
                conn,
                lockbox_id=str(lockbox_id),
                admin=caller.ctx.principal_id or "",
                approve=approve,
                reason=reason,
                now=now,
            )
    except OperatorAccessError as e:
        raise http(e) from e
    return lockbox_view(lb, now)


@router.post("/v1/lockbox-requests/{lockbox_id}:approve")
async def approve(
    lockbox_id: uuid.UUID, body: DecisionBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    return await _decide(request, caller, lockbox_id, True, body.reason)


@router.post("/v1/lockbox-requests/{lockbox_id}:deny")
async def deny(
    lockbox_id: uuid.UUID, body: DecisionBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    return await _decide(request, caller, lockbox_id, False, body.reason)


@router.get("/v1/operator-access-policy")
async def get_policy(request: Request, caller: CallerDep) -> dict[str, Any]:
    tenant = caller.ctx.tenant_id or ""
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn, caller, action=POLICY_ACTION, resource="operator_access_policy", change="read"
        )
        return policy_view(await store.load_policy(conn, tenant))


@router.put("/v1/operator-access-policy")
async def put_policy(body: PolicyBody, request: Request, caller: CallerDep) -> dict[str, Any]:
    jurisdictions = tuple(sorted({j.strip().upper() for j in body.allowed_operator_jurisdictions}))
    if any(len(j) != 2 or not j.isalpha() for j in jurisdictions):
        raise http(
            OperatorAccessError("OPERATOR_SCOPE_INVALID", "Jurisdictions are ISO 3166-1 alpha-2.")
        )
    change = f"lockbox={body.lockbox_required};jurisdictions={','.join(jurisdictions)}"
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn, caller, action=POLICY_ACTION, resource="operator_access_policy", change=change
        )
        p = await service.set_policy(
            conn,
            tenant_id=caller.ctx.tenant_id or "",
            lockbox_required=body.lockbox_required,
            jurisdictions=jurisdictions,
            admin=caller.ctx.principal_id or "",
            now=datetime.now(UTC),
        )
    return policy_view(p)
