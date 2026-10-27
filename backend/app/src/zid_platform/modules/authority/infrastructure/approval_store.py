"""Transaction-bound approvals in PostgreSQL (ZID-MANDATE-001 §16; MAND-16/17, AUTHZ-15/16).

Evaluation always goes through `composite.evaluate_approval` against the transaction as it is *now*,
so a changed value, resource or tool makes every earlier decision stale. The database adds: one
decision per approver per request, decisions bound to the request's transaction hash, append-only.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Row, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.authority.domain.composite import (
    ApprovalDecision,
    ApprovalOutcome,
    ApprovalPolicy,
    ApprovalRequest,
    ApprovalState,
    CompositeMode,
    Verdict,
    evaluate_approval,
)
from zid_platform.modules.authority.domain.grants import AuthorityError


def tx_hash(transaction_context: str) -> bytes:
    return hashlib.sha256(transaction_context.encode("utf-8")).digest()


async def create_request(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    request_id: str,
    requester_id: str,
    subject_ref: str,
    transaction_context: str,
    policy: ApprovalPolicy,
    expires_at: datetime,
    summary: str | None = None,
    action: str | None = None,
    resource: str | None = None,
    change: str | None = None,
) -> None:
    await conn.execute(
        text("""
            INSERT INTO authz.approval_request (approval_request_id, tenant_id, requester_principal_id, subject_ref,
                transaction_context_hash, mode, roles, k, self_approval_forbidden, allow_shared_grant, expires_at,
                summary, action, resource, change)
            VALUES (:id, :t, :req, :sub, :h, :mode, :roles, :k, :saf, :asg, :exp, :sum, :act, :res, :chg)
        """),
        {
            "id": uuid.UUID(request_id),
            "t": uuid.UUID(tenant_id),
            "req": uuid.UUID(requester_id),
            "sub": subject_ref,
            "h": tx_hash(transaction_context),
            "mode": policy.mode.value,
            "roles": list(policy.roles),
            "k": policy.k,
            "saf": policy.self_approval_forbidden,
            "asg": policy.allow_shared_grant,
            "exp": expires_at,
            "sum": summary,
            "act": action,
            "res": resource,
            "chg": change,
        },
    )


async def record_decision(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    request_id: str,
    decision: ApprovalDecision,
    transaction_context: str,
) -> None:
    """One vote per approver per request; a second vote by the same person is refused, not counted."""
    try:
        async with conn.begin_nested():
            await conn.execute(
                text("""
                    INSERT INTO authz.approval_decision (approval_decision_id, tenant_id, approval_request_id,
                        approver_principal_id, approver_roles, authority_grant_id, authority_valid, verdict,
                        transaction_context_hash, session_id, decided_at)
                    VALUES (:id, :t, :r, :who, :roles, :g, :valid, :v, :h, :sess, :at)
                """),
                {
                    "id": uuid.uuid4(),
                    "t": uuid.UUID(tenant_id),
                    "r": uuid.UUID(request_id),
                    "who": uuid.UUID(decision.approver_principal_id),
                    "roles": sorted(decision.approver_roles),
                    "g": uuid.UUID(decision.authority_grant_id),
                    "valid": decision.authority_valid,
                    "v": decision.verdict.value,
                    "h": tx_hash(transaction_context),
                    "sess": decision.session_id,
                    "at": decision.decided_at,
                },
            )
    except IntegrityError as e:
        raise AuthorityError(
            "APPROVAL_DUPLICATE_OR_MISMATCHED",
            "one decision per approver, bound to the request's transaction",
        ) from e


def _request(row: Row[Any]) -> ApprovalRequest:
    m = row._mapping
    return ApprovalRequest(
        approval_request_id=str(m["approval_request_id"]),
        requester_principal_id=str(m["requester_principal_id"]),
        transaction_context_hash=bytes(m["transaction_context_hash"]).hex(),
        policy=ApprovalPolicy(
            CompositeMode(m["mode"]),
            tuple(m["roles"]),
            m["k"],
            m["self_approval_forbidden"],
            m["allow_shared_grant"],
        ),
        expires_at=m["expires_at"],
        external_confirmation_valid=m["external_confirmation_valid"],
    )


def _decision(d: Row[Any]) -> ApprovalDecision:
    return ApprovalDecision(
        approver_principal_id=str(d.approver_principal_id),
        approver_roles=frozenset(d.approver_roles),
        authority_grant_id=str(d.authority_grant_id),
        authority_valid=d.authority_valid,
        verdict=Verdict(d.verdict),
        transaction_context_hash=bytes(d.transaction_context_hash).hex(),
        decided_at=d.decided_at,
        session_id=d.session_id,
    )


async def evaluate(
    conn: AsyncConnection, *, request_id: str, current_transaction_context: str, now: datetime
) -> ApprovalOutcome:
    row = (
        await conn.execute(
            text("SELECT * FROM authz.approval_request WHERE approval_request_id = :r"),
            {"r": uuid.UUID(request_id)},
        )
    ).first()
    if row is None:
        raise AuthorityError("APPROVAL_REQUIRED", "no such approval request")
    req = _request(row)
    rows = (
        await conn.execute(
            text("SELECT * FROM authz.approval_decision WHERE approval_request_id = :r"),
            {"r": uuid.UUID(request_id)},
        )
    ).all()
    return evaluate_approval(
        req,
        [_decision(d) for d in rows],
        current_transaction_context_hash=tx_hash(current_transaction_context).hex(),
        now=now,
    )


def required_approvals(policy: ApprovalPolicy) -> int:
    """How many distinct approvers the policy needs (an external confirmation needs none)."""
    if policy.mode is CompositeMode.SINGLE:
        return 1
    if policy.mode is CompositeMode.K_OF_N:
        return policy.k
    if policy.mode is CompositeMode.EXTERNAL_CONFIRMATION:
        return 0
    return len(policy.roles)  # ALL_OF / ROLE_PAIR: one distinct principal per branch


@dataclass(frozen=True, slots=True)
class RequestSummary:
    request: ApprovalRequest
    subject_ref: str
    created_at: datetime
    outcome: ApprovalOutcome
    approvals_received: int  # distinct principals that voted APPROVE (qualified or not)
    summary: str | None = None  # what the request is for, in words
    action: str | None = None
    resource: str | None = None
    change: str | None = None
    voters: frozenset[str] = frozenset()  # everyone who has already decided on it


async def list_requests(
    conn: AsyncConnection, *, now: datetime, state: ApprovalState | None = None, limit: int = 100
) -> list[RequestSummary]:
    """The tenant's approval requests, newest first, each evaluated for its own bound transaction.

    State is derived, never stored: EXPIRED is selected in SQL; the others are evaluated over the
    live (unexpired, so at most MAX_TTL old) requests and then filtered."""
    if state is ApprovalState.EXPIRED:
        cond, lim = "expires_at <= :now", limit
    elif state is not None:
        cond, lim = "expires_at > :now", None
    else:
        cond, lim = "TRUE", limit
    rows = (
        await conn.execute(
            text(
                f"SELECT * FROM authz.approval_request WHERE {cond} "
                "ORDER BY created_at DESC, approval_request_id DESC"
                + (" LIMIT :lim" if lim is not None else "")
            ),
            {"now": now, "lim": lim},
        )
    ).all()
    if not rows:
        return []
    by_request: dict[str, list[ApprovalDecision]] = {}
    for d in (
        await conn.execute(
            text("SELECT * FROM authz.approval_decision WHERE approval_request_id = ANY(:ids)"),
            {"ids": [r.approval_request_id for r in rows]},
        )
    ).all():
        by_request.setdefault(str(d.approval_request_id), []).append(_decision(d))
    out: list[RequestSummary] = []
    for r in rows:
        req = _request(r)
        decisions = by_request.get(req.approval_request_id, [])
        outcome = evaluate_approval(
            req, decisions, current_transaction_context_hash=req.transaction_context_hash, now=now
        )
        if state is not None and outcome.state is not state:
            continue
        approvers = {d.approver_principal_id for d in decisions if d.verdict is Verdict.APPROVE}
        out.append(
            RequestSummary(
                req,
                r.subject_ref,
                r.created_at,
                outcome,
                len(approvers),
                r.summary,
                r.action,
                r.resource,
                r.change,
                frozenset(d.approver_principal_id for d in decisions),
            )
        )
        if len(out) >= limit:
            break
    return out
