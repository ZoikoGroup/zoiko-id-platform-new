"""Transaction-bound approval API (ZID-API-001 §10.3; ZID-MANDATE-001 §16; MAND-16/17, AUTHZ-15/16).

    GET  /v1/approval-requests                     list, newest first   (authority.approval.read, R1)
    POST /v1/approval-requests                     open a request bound to one transaction context
    POST /v1/approval-requests/{id}/decisions      the caller approves or denies
    GET  /v1/approval-requests/{id}?transaction_context=...   current outcome for that exact transaction
    GET  /v1/approval-requests/awaiting-me         what the caller may approve now, with a plain summary
    GET  /v1/approval-requests/for-change          the caller's own latest request for one exact change

Nothing about the approver is taken from the body except which of their grants they act under: the
approver is the authenticated principal, the grant must be theirs, and its validity and the roles it
confers (the actions of its effective authority) are resolved server-side at decision time.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from zid_context import uuid7
from zid_db import tenant_transaction

from zid_platform.api_auth import Caller, authenticated_caller, http_error, request_engine
from zid_platform.modules.authority.application.service import resolve
from zid_platform.modules.authority.domain.algebra import ANY
from zid_platform.modules.authority.domain.composite import (
    ApprovalDecision,
    ApprovalOutcome,
    ApprovalPolicy,
    ApprovalState,
    CompositeMode,
    Verdict,
)
from zid_platform.modules.authority.domain.grants import AuthorityError, GrantStatus
from zid_platform.modules.authority.infrastructure import approval_store
from zid_platform.modules.authority.infrastructure.grant_store import (
    load_chain,
    load_grants_for_grantee,
)
from zid_platform.protected import protected_action, transaction_context

router = APIRouter(prefix="/v1/approval-requests", tags=["authority"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]
MAX_TTL = timedelta(days=7)


class RequestCreate(BaseModel):
    """Either the exact `transaction_context`, or the `action`, `resource` and `change` it is made of (the app
    builds the context itself, so nobody pastes a string). `summary` says in words what the request is for."""

    model_config = ConfigDict(extra="forbid")
    subject_ref: str = Field(min_length=1, max_length=256)  # e.g. "payment:pay_123"
    transaction_context: str | None = Field(default=None, min_length=1, max_length=4096)
    action: str | None = Field(default=None, min_length=1, max_length=128)
    resource: str | None = Field(default=None, min_length=1, max_length=256)
    change: str = Field(default="", max_length=2048)
    summary: str | None = Field(default=None, min_length=1, max_length=300)
    mode: CompositeMode = CompositeMode.SINGLE
    roles: list[str] = Field(min_length=1)
    k: int = Field(default=1, ge=1, le=16)
    ttl_seconds: int = Field(default=3600, ge=60, le=int(MAX_TTL.total_seconds()))


class DecisionCreate(BaseModel):
    """`authority_grant_id` and `transaction_context` may be left out for a request that records its action,
    resource and change: the grant is then the caller's own qualifying one and the context is rebuilt from the
    request, so approving is one click on a readable summary."""

    model_config = ConfigDict(extra="forbid")
    verdict: Verdict
    authority_grant_id: uuid.UUID | None = None
    transaction_context: str | None = Field(default=None, min_length=1, max_length=4096)
    risk_class: int = Field(default=3, ge=0, le=3)


def _outcome(o: ApprovalOutcome) -> dict[str, Any]:
    return {"state": o.state.value, "codes": list(o.codes), "counted_approvers": len(o.counted)}


def _summary(s: approval_store.RequestSummary) -> dict[str, Any]:
    """No transaction context, not even its hash: only what the request is about and where it is."""
    r = s.request
    return {
        "approval_request_id": r.approval_request_id,
        "subject_ref": s.subject_ref,
        "requester_principal_id": r.requester_principal_id,
        "mode": r.policy.mode.value,
        "roles": list(r.policy.roles),
        "state": s.outcome.state.value,
        "codes": list(s.outcome.codes),
        "required_approvals": approval_store.required_approvals(r.policy),
        "approvals_received": s.approvals_received,
        "summary": s.summary,
        "action": s.action,
        "resource": s.resource,
        "change": s.change,
        "created_at": s.created_at.isoformat(),
        "expires_at": r.expires_at.isoformat(),
    }


@router.get("")
async def list_all(
    request: Request,
    caller: CallerDep,
    state: ApprovalState | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn, caller, action="authority.approval.read", resource="approval_request:*"
        )
        items = await approval_store.list_requests(
            conn, now=datetime.now(UTC), state=state, limit=limit
        )
    return {"items": [_summary(s) for s in items]}


@router.post("", status_code=201)
async def create(body: RequestCreate, request: Request, caller: CallerDep) -> dict[str, Any]:
    if not caller.ctx.principal_id:
        raise http_error("APPROVER_IDENTITY_MISSING", "An authenticated principal is required.")
    try:  # self-approval forbidden and no shared grants: not client-selectable (MAND-17)
        policy = ApprovalPolicy(body.mode, tuple(body.roles), body.k)
    except ValueError as e:
        raise http_error("VALIDATION_FAILED", str(e)) from e
    if body.action and body.resource:
        context = transaction_context(body.action, body.resource, body.change)
        if body.transaction_context not in (None, context):
            raise http_error(
                "VALIDATION_FAILED",
                "transaction_context does not match action, resource and change",
            )
    elif body.transaction_context:
        context = body.transaction_context
    else:
        raise http_error(
            "VALIDATION_FAILED", "give action and resource, or the transaction context"
        )
    rid, expires = str(uuid7()), datetime.now(UTC) + timedelta(seconds=body.ttl_seconds)
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await approval_store.create_request(
            conn,
            tenant_id=caller.ctx.tenant_id or "",
            request_id=rid,
            requester_id=caller.ctx.principal_id or "",
            subject_ref=body.subject_ref,
            transaction_context=context,
            policy=policy,
            expires_at=expires,
            summary=body.summary,
            action=body.action,
            resource=body.resource,
            change=body.change if body.action and body.resource else None,
        )
    return {"approval_request_id": rid, "state": "PENDING", "expires_at": expires.isoformat()}


async def _qualifying_grant(
    conn: Any, tenant: str, me: str, roles: tuple[str, ...], now: datetime
) -> tuple[str, Any] | None:
    """The caller's own active grant whose effective actions include one of `roles`, with those actions."""
    for g in await load_grants_for_grantee(conn, me):
        if g.status is not GrantStatus.ACTIVE:
            continue
        res = await resolve(conn, tenant_id=tenant, grant_id=g.grant_id, risk_level=3, now=now)
        if not (res.valid and res.effective):
            continue
        acts = res.effective.actions
        if acts is ANY:
            return g.grant_id, acts
        if isinstance(acts, frozenset) and any(r in acts for r in roles):
            return g.grant_id, acts
    return None


@router.get("/awaiting-me")
async def awaiting_me(request: Request, caller: CallerDep) -> dict[str, Any]:
    """Pending requests the caller did not open, has not decided, and holds authority to decide."""
    tenant, me, now = caller.ctx.tenant_id or "", caller.ctx.principal_id or "", datetime.now(UTC)
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn, caller, action="authority.approval.read", resource="approval_request:*"
        )
        pending = await approval_store.list_requests(
            conn, now=now, state=ApprovalState.PENDING, limit=100
        )
        out = []
        for s in pending:
            if s.request.requester_principal_id == me or me in s.voters:
                continue
            found = await _qualifying_grant(conn, tenant, me, s.request.policy.roles, now)
            if found is None:
                continue
            out.append({**_summary(s), "authority_grant_id": found[0]})
    return {"items": out}


@router.get("/for-change")
async def for_change(
    request: Request,
    caller: CallerDep,
    action: Annotated[str, Query(min_length=1, max_length=128)],
    resource: Annotated[str, Query(min_length=1, max_length=256)],
    change: Annotated[str, Query(max_length=2048)] = "",
) -> dict[str, Any]:
    """The caller's own most recent unexpired request for exactly this change, or null."""
    me, now = caller.ctx.principal_id or "", datetime.now(UTC)
    want = approval_store.tx_hash(transaction_context(action, resource, change)).hex()
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn, caller, action="authority.approval.read", resource="approval_request:*"
        )
        for s in await approval_store.list_requests(conn, now=now, limit=100):
            mine = s.request.requester_principal_id == me
            if (
                mine
                and s.request.transaction_context_hash == want
                and s.outcome.state is not ApprovalState.EXPIRED
            ):
                return {"request": _summary(s)}
    return {"request": None}


@router.post("/{request_id}/decisions", status_code=201)
async def decide(
    request_id: uuid.UUID, body: DecisionCreate, request: Request, caller: CallerDep
) -> dict[str, Any]:
    tenant, me, now = caller.ctx.tenant_id or "", caller.ctx.principal_id or "", datetime.now(UTC)
    if not me:
        raise http_error("APPROVER_IDENTITY_MISSING", "An authenticated principal is required.")
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        context = body.transaction_context
        gid = str(body.authority_grant_id) if body.authority_grant_id else None
        if context is None or gid is None:
            # the guided path: the request itself records what it is bound to and who may decide it
            stored = next(
                (
                    s
                    for s in await approval_store.list_requests(conn, now=now, limit=500)
                    if s.request.approval_request_id == str(request_id)
                ),
                None,
            )
            if stored is None:
                raise http_error("APPROVAL_REQUEST_NOT_FOUND", "No such approval request.")
            if context is None:
                if not (stored.action and stored.resource):
                    raise http_error(
                        "VALIDATION_FAILED", "transaction_context is required for this request"
                    )
                context = transaction_context(stored.action, stored.resource, stored.change or "")
            if gid is None:
                found = await _qualifying_grant(conn, tenant, me, stored.request.policy.roles, now)
                if found is None:
                    raise http_error(
                        "APPROVER_GRANT_NOT_HELD",
                        "You hold no authority that can approve this request.",
                    )
                gid = found[0]
        chain = await load_chain(conn, gid)
        if gid not in chain:
            raise http_error("AUTHORITY_GRANT_NOT_FOUND")
        if chain[gid].grantee_principal_id != me:
            raise http_error(
                "APPROVER_GRANT_NOT_HELD", "Approvers act only under their own grants."
            )
        res = await resolve(
            conn, tenant_id=tenant, grant_id=gid, risk_level=body.risk_class, now=now
        )
        acts = res.effective.actions if res.valid and res.effective else frozenset()
        decision = ApprovalDecision(
            approver_principal_id=me,
            approver_roles=frozenset() if acts is ANY else frozenset(acts),  # type: ignore[arg-type]
            authority_grant_id=gid,
            authority_valid=res.valid,
            verdict=body.verdict,
            transaction_context_hash=approval_store.tx_hash(context).hex(),
            decided_at=now,
            session_id=caller.ctx.request_id,
        )
        try:
            await approval_store.record_decision(
                conn,
                tenant_id=tenant,
                request_id=str(request_id),
                decision=decision,
                transaction_context=context,
            )
            outcome = await approval_store.evaluate(
                conn,
                request_id=str(request_id),
                current_transaction_context=context,
                now=now,
            )
        except AuthorityError as e:
            raise http_error(e.code, str(e)) from e
    return {
        "approval_request_id": str(request_id),
        "authority_valid": res.valid,
        **_outcome(outcome),
    }


@router.get("/{request_id}")
async def read(
    request_id: uuid.UUID,
    request: Request,
    caller: CallerDep,
    transaction_context: Annotated[str, Query(min_length=1, max_length=4096)],
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        try:
            outcome = await approval_store.evaluate(
                conn,
                request_id=str(request_id),
                current_transaction_context=transaction_context,
                now=datetime.now(UTC),
            )
        except AuthorityError as e:
            code = "APPROVAL_REQUEST_NOT_FOUND" if e.code == "APPROVAL_REQUIRED" else e.code
            raise http_error(code, str(e)) from e
    return {"approval_request_id": str(request_id), **_outcome(outcome)}
