"""Transaction-bound approvals in PostgreSQL (ZID-MANDATE-001 §16; MAND-16/17, AUTHZ-15/16).

Evaluation always goes through `composite.evaluate_approval` against the transaction as it is *now*,
so a changed value, resource or tool makes every earlier decision stale. The database adds: one
decision per approver per request, decisions bound to the request's transaction hash, append-only.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.authority.domain.composite import (
    ApprovalDecision,
    ApprovalOutcome,
    ApprovalPolicy,
    ApprovalRequest,
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
) -> None:
    await conn.execute(
        text("""
            INSERT INTO authz.approval_request (approval_request_id, tenant_id, requester_principal_id, subject_ref,
                transaction_context_hash, mode, roles, k, self_approval_forbidden, allow_shared_grant, expires_at)
            VALUES (:id, :t, :req, :sub, :h, :mode, :roles, :k, :saf, :asg, :exp)
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
    m = row._mapping
    req = ApprovalRequest(
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
    rows = (
        await conn.execute(
            text("SELECT * FROM authz.approval_decision WHERE approval_request_id = :r"),
            {"r": uuid.UUID(request_id)},
        )
    ).all()
    decisions = [
        ApprovalDecision(
            approver_principal_id=str(d.approver_principal_id),
            approver_roles=frozenset(d.approver_roles),
            authority_grant_id=str(d.authority_grant_id),
            authority_valid=d.authority_valid,
            verdict=Verdict(d.verdict),
            transaction_context_hash=bytes(d.transaction_context_hash).hex(),
            decided_at=d.decided_at,
            session_id=d.session_id,
        )
        for d in rows
    ]
    return evaluate_approval(
        req,
        decisions,
        current_transaction_context_hash=tx_hash(current_transaction_context).hex(),
        now=now,
    )
