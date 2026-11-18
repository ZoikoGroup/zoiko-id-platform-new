"""A company's WF-05 verification policy: write, submit, decide, list, and the approved one in force (ZID-DTV-WF-05 A11; CR-118).

Runs in the caller's tenant transaction (row-level security). The approved policy is read when a case opens (its required checks are added) and when a
proof of address document is added (its accepted types and ages narrow the platform's).
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.verification.domain import address as ad
from zid_platform.modules.verification.domain import check_methods as cm
from zid_platform.modules.verification.domain import check_policy as cp
from zid_platform.modules.verification.infrastructure.address_policy_registry import proof_policy

_COLS = "policy_id, market, purpose, required_methods, document_types, status, author, created_at, approver, decided_at, decision_reason"


def _row(r: Any) -> cp.Policy:
    types = r.document_types if isinstance(r.document_types, dict) else json.loads(r.document_types)
    return cp.Policy(
        str(r.policy_id), r.market, r.purpose, tuple(cm.CheckMethod(m) for m in r.required_methods), {k: int(v) for k, v in types.items()},
        cp.Status(r.status), r.author, r.created_at, r.approver, r.decided_at, r.decision_reason,
    )  # fmt: skip


def view(p: cp.Policy) -> dict[str, Any]:
    return {
        "policy_id": p.policy_id,
        "market": p.market,
        "purpose": p.purpose,
        "required_methods": [m.value for m in p.required_methods],
        "document_types": dict(p.document_types),
        "status": p.status.value,
        "author": p.author,
        "approver": p.approver,
        "created_at": p.created_at.isoformat(),
        "decided_at": p.decided_at.isoformat() if p.decided_at else None,
        "decision_reason": p.decision_reason,
    }


async def _load(conn: AsyncConnection, policy_id: str) -> cp.Policy:
    try:
        pid = uuid.UUID(policy_id)
    except ValueError as e:
        raise cp.PolicyError("POLICY_NOT_FOUND") from e
    row = (
        await conn.execute(
            text(f"SELECT {_COLS} FROM verify.check_policy WHERE policy_id = :p FOR UPDATE"),
            {"p": pid},
        )
    ).first()
    if row is None:
        raise cp.PolicyError("POLICY_NOT_FOUND")
    return _row(row)


async def approved(conn: AsyncConnection, market: str, purpose: str) -> cp.Policy | None:
    row = (
        await conn.execute(
            text(
                f"SELECT {_COLS} FROM verify.check_policy WHERE market = :m AND purpose = :p AND status = 'APPROVED'"
            ),
            {"m": market.upper(), "p": purpose},
        )
    ).first()
    return _row(row) if row else None


async def create(
    conn: AsyncConnection, tenant_id: str, *, market: str, purpose: str, required: list[cm.CheckMethod], document_types: dict[str, int], by: str,
    now: datetime,
) -> cp.Policy:  # fmt: skip
    p = cp.draft(
        policy_id=str(uuid.uuid4()), market=market, purpose=purpose, required=required, document_types=document_types,
        platform_types=proof_policy().rules_for(market.upper()), author=by, now=now,
    )  # fmt: skip
    await conn.execute(
        text(
            f"INSERT INTO verify.check_policy (tenant_id, {_COLS}) VALUES (:t, :id, :m, :pu, :req, CAST(:dt AS jsonb), 'DRAFT', :a, :at, NULL, NULL, NULL)"
        ),
        {
            "t": uuid.UUID(tenant_id), "id": uuid.UUID(p.policy_id), "m": p.market, "pu": p.purpose, "req": [m.value for m in p.required_methods],
            "dt": json.dumps(dict(p.document_types)), "a": p.author, "at": now,
        },
    )  # fmt: skip
    return p


async def submit(conn: AsyncConnection, policy_id: str, *, by: str) -> cp.Policy:
    p = cp.submit(await _load(conn, policy_id), by=by)
    await conn.execute(
        text("UPDATE verify.check_policy SET status = 'SUBMITTED' WHERE policy_id = :p"),
        {"p": uuid.UUID(policy_id)},
    )
    return p


async def decide(
    conn: AsyncConnection, policy_id: str, *, approve: bool, by: str, reason: str, now: datetime
) -> cp.Policy:
    p = cp.decide(await _load(conn, policy_id), approve=approve, by=by, reason=reason, now=now)
    if approve:
        await conn.execute(
            text(
                "UPDATE verify.check_policy SET status = 'SUPERSEDED' WHERE market = :m AND purpose = :pu AND status = 'APPROVED'"
            ),
            {"m": p.market, "pu": p.purpose},
        )
    await conn.execute(
        text(
            "UPDATE verify.check_policy SET status = :s, approver = :a, decided_at = :d, decision_reason = :r WHERE policy_id = :p"
        ),
        {"s": p.status.value, "a": by, "d": now, "r": reason, "p": uuid.UUID(policy_id)},
    )
    return p


async def listing(conn: AsyncConnection) -> list[cp.Policy]:
    rows = (
        await conn.execute(
            text(f"SELECT {_COLS} FROM verify.check_policy ORDER BY created_at DESC LIMIT 100")
        )
    ).all()
    return [_row(r) for r in rows]


def accepted_for(market: str, company: cp.Policy | None) -> dict[str, int]:
    return cp.effective_types(proof_policy().rules_for(market.upper()), company)


def narrowed_proof_policy(market: str, company: cp.Policy | None) -> ad.ProofPolicy:
    """The platform's proof policy with this market's types narrowed by the company's approved policy."""
    base = proof_policy()
    markets = {k: dict(v) for k, v in base.markets.items()}
    if (
        market.upper() in markets
    ):  # a market the platform does not name stays unnamed (NO_POLICY_FOR_MARKET)
        markets[market.upper()] = accepted_for(market, company)
    return ad.ProofPolicy(
        base.version, base.status, markets, base.approved_by, base.legal_basis_ref
    )


__all__ = [
    "accepted_for",
    "approved",
    "create",
    "decide",
    "listing",
    "narrowed_proof_policy",
    "submit",
    "view",
]
