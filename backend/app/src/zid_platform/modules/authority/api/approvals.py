"""Transaction-bound approval API (ZID-API-001 §10.3; ZID-MANDATE-001 §16; MAND-16/17, AUTHZ-15/16).

    POST /v1/approval-requests                     open a request bound to one transaction context
    POST /v1/approval-requests/{id}/decisions      the caller approves or denies
    GET  /v1/approval-requests/{id}?transaction_context=...   current outcome for that exact transaction

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
    CompositeMode,
    Verdict,
)
from zid_platform.modules.authority.domain.grants import AuthorityError
from zid_platform.modules.authority.infrastructure import approval_store
from zid_platform.modules.authority.infrastructure.grant_store import load_chain

router = APIRouter(prefix="/v1/approval-requests", tags=["authority"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]
MAX_TTL = timedelta(days=7)


class RequestCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subject_ref: str = Field(min_length=1, max_length=256)  # e.g. "payment:pay_123"
    transaction_context: str = Field(
        min_length=1, max_length=4096
    )  # canonical form; stored only as a hash
    mode: CompositeMode = CompositeMode.SINGLE
    roles: list[str] = Field(min_length=1)
    k: int = Field(default=1, ge=1, le=16)
    ttl_seconds: int = Field(default=3600, ge=60, le=int(MAX_TTL.total_seconds()))


class DecisionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    verdict: Verdict
    authority_grant_id: uuid.UUID
    transaction_context: str = Field(min_length=1, max_length=4096)
    risk_class: int = Field(default=3, ge=0, le=3)


def _outcome(o: ApprovalOutcome) -> dict[str, Any]:
    return {"state": o.state.value, "codes": list(o.codes), "counted_approvers": len(o.counted)}


@router.post("", status_code=201)
async def create(body: RequestCreate, request: Request, caller: CallerDep) -> dict[str, Any]:
    if not caller.ctx.principal_id:
        raise http_error("APPROVER_IDENTITY_MISSING", "An authenticated principal is required.")
    try:  # self-approval forbidden and no shared grants: not client-selectable (MAND-17)
        policy = ApprovalPolicy(body.mode, tuple(body.roles), body.k)
    except ValueError as e:
        raise http_error("VALIDATION_FAILED", str(e)) from e
    rid, expires = str(uuid7()), datetime.now(UTC) + timedelta(seconds=body.ttl_seconds)
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await approval_store.create_request(
            conn,
            tenant_id=caller.ctx.tenant_id or "",
            request_id=rid,
            requester_id=caller.ctx.principal_id or "",
            subject_ref=body.subject_ref,
            transaction_context=body.transaction_context,
            policy=policy,
            expires_at=expires,
        )
    return {"approval_request_id": rid, "state": "PENDING", "expires_at": expires.isoformat()}


@router.post("/{request_id}/decisions", status_code=201)
async def decide(
    request_id: uuid.UUID, body: DecisionCreate, request: Request, caller: CallerDep
) -> dict[str, Any]:
    tenant, me, now = caller.ctx.tenant_id or "", caller.ctx.principal_id or "", datetime.now(UTC)
    gid = str(body.authority_grant_id)
    if not me:
        raise http_error("APPROVER_IDENTITY_MISSING", "An authenticated principal is required.")
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
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
            transaction_context_hash=approval_store.tx_hash(body.transaction_context).hex(),
            decided_at=now,
            session_id=caller.ctx.request_id,
        )
        try:
            await approval_store.record_decision(
                conn,
                tenant_id=tenant,
                request_id=str(request_id),
                decision=decision,
                transaction_context=body.transaction_context,
            )
            outcome = await approval_store.evaluate(
                conn,
                request_id=str(request_id),
                current_transaction_context=body.transaction_context,
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
