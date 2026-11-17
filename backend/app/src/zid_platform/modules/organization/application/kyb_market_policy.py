"""Publishing company-verification market policy packs (ZID-DTV-WF-02 K09; CR-109). Orchestration over `domain/market_policy.py`; the rules live there.

`propose` stores a version (identified by the digest of its content, never changed after). `approve` records one sign-off bound to that digest. `activate` publishes: it checks every control of
`publish_problems` against the stored approvals and refuses with the full list of what is missing, then closes the market's open interval and opens a new one. `rollback` re-activates a version that was
in force before, again as a new interval with a note. `effective_at` answers which pack was in force at a moment, so a decision replays against the pack that made it. Nothing earlier is rewritten.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection
from zid_outbox import emit

from zid_platform.modules.organization.domain import market_policy as mp


class PolicyError(Exception):
    def __init__(self, code: str, problems: tuple[str, ...] = ()) -> None:
        super().__init__(f"{code}: {', '.join(problems)}" if problems else code)
        self.code = code
        self.problems = problems


def market_key(p: mp.MarketPolicy) -> str:
    """The pack's market without its version: one jurisdiction, region, legal form, purpose and risk tier."""
    return "|".join((p.jurisdiction, p.region or "-", p.legal_form, p.purpose, p.risk_tier))


async def propose(conn: AsyncConnection, policy: mp.MarketPolicy, *, author: str) -> str:
    """Store a version. The same content proposed again is the same version; the same version name with different content is refused."""
    digest = mp.policy_hash(policy)
    key = market_key(policy)
    existing = (
        await conn.execute(
            text(
                "SELECT policy_hash FROM org.kyb_market_policy WHERE market_key = :k AND version = :v"
            ),
            {"k": key, "v": policy.version},
        )
    ).first()
    if existing is not None:
        if existing.policy_hash != digest:
            raise PolicyError("POLICY_VERSION_EXISTS", (policy.version,))
        return digest
    await conn.execute(
        text(
            "INSERT INTO org.kyb_market_policy (policy_hash, market_key, version, body, author) VALUES (:h, :k, :v, CAST(:b AS jsonb), :a)"
        ),
        {
            "h": digest,
            "k": key,
            "v": policy.version,
            "b": json.dumps(mp.policy_to_dict(policy), sort_keys=True, separators=(",", ":")),
            "a": uuid.UUID(author),
        },
    )
    return digest


async def load(conn: AsyncConnection, policy_hash: str) -> tuple[mp.MarketPolicy, str]:
    """The stored pack and its author. A pack whose digest no longer matches its content, or that cannot be read, is refused: it is never used."""
    row = (
        await conn.execute(
            text("SELECT body, author FROM org.kyb_market_policy WHERE policy_hash = :h"),
            {"h": policy_hash},
        )
    ).first()
    if row is None:
        raise PolicyError("POLICY_NOT_FOUND", (policy_hash,))
    try:
        policy = mp.policy_from_dict(row.body)
    except ValueError as e:
        raise PolicyError("POLICY_UNREADABLE") from e
    if mp.policy_hash(policy) != policy_hash:
        raise PolicyError("POLICY_DIGEST_MISMATCH", (policy_hash,))
    return policy, str(row.author)


async def approve(conn: AsyncConnection, policy_hash: str, *, person: str, role: str) -> None:
    if role not in mp.SIGN_OFF_ROLES:
        raise PolicyError("SIGN_OFF_ROLE_INVALID", (role,))
    await load(conn, policy_hash)
    await conn.execute(
        text(
            "INSERT INTO org.kyb_market_policy_approval (approval_id, policy_hash, person, role) VALUES (:i, :h, :p, :r) ON CONFLICT (policy_hash, person, role) DO NOTHING"
        ),
        {"i": uuid.uuid4(), "h": policy_hash, "p": uuid.UUID(person), "r": role},
    )


async def _approvals(conn: AsyncConnection, policy_hash: str) -> list[mp.Approval]:
    rows = (
        await conn.execute(
            text("SELECT person, role FROM org.kyb_market_policy_approval WHERE policy_hash = :h"),
            {"h": policy_hash},
        )
    ).all()
    return [mp.Approval(str(r.person), r.role, policy_hash) for r in rows]


async def _open(conn: AsyncConnection, key: str) -> Any:
    return (
        await conn.execute(
            text(
                "SELECT activation_id, policy_hash FROM org.kyb_market_policy_activation WHERE market_key = :k AND ended_at IS NULL FOR UPDATE"
            ),
            {"k": key},
        )
    ).first()


async def _switch(
    conn: AsyncConnection,
    key: str,
    policy_hash: str,
    *,
    kind: str,
    by: str,
    note: str | None,
    now: datetime,
) -> None:
    current = await _open(conn, key)
    if current is not None:
        await conn.execute(
            text(
                "UPDATE org.kyb_market_policy_activation SET ended_at = :n WHERE activation_id = :i"
            ),
            {"n": now, "i": current.activation_id},
        )
    await conn.execute(
        text(
            "INSERT INTO org.kyb_market_policy_activation (activation_id, market_key, policy_hash, kind, activated_by, note, activated_at) VALUES (:i, :k, :h, :kd, :by, :nt, :n)"
        ),
        {
            "i": uuid.uuid4(),
            "k": key,
            "h": policy_hash,
            "kd": kind,
            "by": uuid.UUID(by),
            "nt": note,
            "n": now,
        },
    )


async def activate(
    conn: AsyncConnection, policy_hash: str, *, by: str, tenant_id: str, now: datetime | None = None
) -> None:
    """Publish a stored version. Refused with every missing control named (`POLICY_NOT_PUBLISHABLE`) unless the pack is complete, its checks pass, the four sign-offs are in from people other than the
    author and, for a high-impact change, two different approvers have signed."""
    now = now or datetime.now(UTC)
    draft, author = await load(conn, policy_hash)
    key = market_key(draft)
    current = await _open(conn, key)
    previous = (await load(conn, current.policy_hash))[0] if current is not None else None
    problems = mp.publish_problems(
        draft,
        previous,
        author=author,
        approvals=await _approvals(conn, policy_hash),
        tests_passed=not mp.activation_problems(draft),
    )  # the pack's own checks are the completeness rules: a pack that fails them has not passed its tests
    if problems:
        raise PolicyError("POLICY_NOT_PUBLISHABLE", tuple(problems))
    if current is not None and current.policy_hash == policy_hash:
        raise PolicyError("POLICY_ALREADY_ACTIVE", (policy_hash,))
    await _switch(conn, key, policy_hash, kind="PUBLISH", by=by, note=None, now=now)
    await _announce(conn, tenant_id, key, policy_hash, "PUBLISH", now)


async def rollback(
    conn: AsyncConnection,
    key: str,
    to_hash: str,
    *,
    by: str,
    reason: str,
    tenant_id: str,
    now: datetime | None = None,
) -> None:
    """Re-activate a version that was in force before, as a new interval with the reason. A version that was never active cannot be rolled back to."""
    now = now or datetime.now(UTC)
    if len(reason.strip()) < 10:
        raise PolicyError("REASON_REQUIRED")
    was = (
        await conn.execute(
            text(
                "SELECT 1 FROM org.kyb_market_policy_activation WHERE market_key = :k AND policy_hash = :h LIMIT 1"
            ),
            {"k": key, "h": to_hash},
        )
    ).first()
    if was is None:
        raise PolicyError("POLICY_NEVER_ACTIVE", (to_hash,))
    current = await _open(conn, key)
    if current is not None and current.policy_hash == to_hash:
        raise PolicyError("POLICY_ALREADY_ACTIVE", (to_hash,))
    await load(conn, to_hash)  # still readable and still matching its digest
    await _switch(conn, key, to_hash, kind="ROLLBACK", by=by, note=reason.strip(), now=now)
    await _announce(conn, tenant_id, key, to_hash, "ROLLBACK", now)


async def effective_at(
    conn: AsyncConnection, key: str, moment: datetime
) -> tuple[str, mp.MarketPolicy] | None:
    """The pack that was in force for this market at a moment, or None: a market with no published pack is not enabled."""
    row = (
        await conn.execute(
            text(
                "SELECT policy_hash FROM org.kyb_market_policy_activation WHERE market_key = :k AND activated_at <= :m AND (ended_at IS NULL OR :m < ended_at) "
                "ORDER BY activated_at DESC LIMIT 1"
            ),
            {"k": key, "m": moment},
        )
    ).first()
    if row is None:
        return None
    return row.policy_hash, (await load(conn, row.policy_hash))[0]


async def retention_days_for(
    conn: AsyncConnection,
    jurisdiction: str,
    legal_form: str | None,
    *,
    record_class: str = "RAW_DOCUMENTS",
    now: datetime | None = None,
) -> int | None:
    """How long the PUBLISHED pack for this company's market keeps a class of record, or None when no pack is published for it (the caller then keeps its own setting, which is "until the owners
    decide" unless they have set one). The key uses the proposed purpose and risk tier of `config/kyb/market-defaults.yaml`."""
    from zid_platform.modules.organization.infrastructure import market_defaults

    purpose, tier = market_defaults.load()
    key = "|".join((jurisdiction, "-", (legal_form or "").upper(), purpose, tier))
    found = await effective_at(conn, key, now or datetime.now(UTC))
    return None if found is None else found[1].retention.days.get(record_class)


async def versions(conn: AsyncConnection) -> list[dict[str, Any]]:
    """Every stored version, newest first: who proposed it, who has signed in which role, and whether it is the one in force for its market."""
    rows = (
        await conn.execute(
            text(
                "SELECT p.policy_hash, p.market_key, p.version, p.author, p.created_at, "
                "coalesce((SELECT array_agg(a.role ORDER BY a.role) FROM org.kyb_market_policy_approval a WHERE a.policy_hash = p.policy_hash), ARRAY[]::text[]) AS roles, "
                "EXISTS (SELECT 1 FROM org.kyb_market_policy_activation v WHERE v.policy_hash = p.policy_hash AND v.ended_at IS NULL) AS active "
                "FROM org.kyb_market_policy p ORDER BY p.created_at DESC, p.version DESC LIMIT 200"
            )
        )
    ).all()
    return [
        {"policy_hash": r.policy_hash, "market_key": r.market_key, "version": r.version, "author": str(r.author), "created_at": r.created_at.isoformat(), "approved_roles": list(r.roles), "active": r.active}
        for r in rows
    ]  # fmt: skip


async def describe(conn: AsyncConnection, policy_hash: str) -> dict[str, Any]:
    """One version with everything that still stops it being published, by code: what the people who own each control need to see."""
    draft, author = await load(conn, policy_hash)
    key = market_key(draft)
    current = await _open(conn, key)
    previous = (await load(conn, current.policy_hash))[0] if current is not None else None
    approvals = await _approvals(conn, policy_hash)
    problems = mp.publish_problems(
        draft,
        previous,
        author=author,
        approvals=approvals,
        tests_passed=not mp.activation_problems(draft),
    )
    return {
        "policy_hash": policy_hash,
        "market_key": key,
        "version": draft.version,
        "author": author,
        "pack": mp.policy_to_dict(draft),
        "approved": sorted({(a.role, a.person) for a in approvals}),
        "high_impact_changes": mp.high_impact_changes(previous, draft),
        "publishable": not problems,
        "problems": problems,
        "in_force": current is not None and current.policy_hash == policy_hash,
    }


async def _announce(
    conn: AsyncConnection, tenant_id: str, key: str, policy_hash: str, kind: str, now: datetime
) -> None:
    await emit(
        conn,
        name="organization.kyb.policy_activated",
        tenant_id=tenant_id,
        aggregate_type="kyb_market_policy",
        aggregate_id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"kyb-policy:{key}")),
        aggregate_version=int(now.timestamp() * 1000),
        data={"market_key": key, "policy_hash": policy_hash, "kind": kind},
    )
