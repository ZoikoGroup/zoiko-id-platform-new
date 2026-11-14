"""Storage for agent governance (ZID-AG-WF-002; CR-091; machine_0008). Tenant transactions, RLS on, SQL only.

Atomic reservation: `lock_ceilings` takes a row lock on every budget row above a call, in id order, so two calls that compete
for the last of an allowance queue behind each other and the second one reads the first one's reservation. The sums are read
inside the same transaction as the insert. Nothing here decides anything; `domain.governance` does.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.machine.domain import governance as g


def _u(value: str | None) -> uuid.UUID | None:
    return uuid.UUID(value) if value else None


def _j(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _row(row: Any) -> dict[str, Any]:
    return dict(row._mapping)


# ---------------------------------------------------------------- policies


async def next_policy_version(conn: AsyncConnection, agent_id: str) -> int:
    row = (
        await conn.execute(
            text(
                "SELECT coalesce(max(version), 0) + 1 FROM agent.governance_policy WHERE agent_principal_id = :a"
            ),
            {"a": uuid.UUID(agent_id)},
        )
    ).first()
    return int(row[0])  # type: ignore[index]


async def insert_policy(
    conn: AsyncConnection, tenant_id: str, policy: g.GovernancePolicy, *, created_by: str
) -> None:
    await conn.execute(
        text(
            "INSERT INTO agent.governance_policy (policy_id, tenant_id, agent_principal_id, version, risk_tier, state, region, "
            "content, policy_hash, expires_at, created_by) VALUES (:p, :t, :a, :v, :r, :s, :reg, CAST(:c AS jsonb), :h, :e, :by)"
        ),
        {
            "p": uuid.UUID(policy.policy_id),
            "t": uuid.UUID(tenant_id),
            "a": uuid.UUID(policy.agent_id),
            "v": policy.version,
            "r": policy.risk_tier,
            "s": policy.state,
            "reg": policy.region,
            "c": _j(
                {
                    **g.policy_content(policy),
                    "leases": [{**g.grant_entry(x), "grant_id": x.grant_id} for x in policy.grants],
                }
            ),
            "h": g.policy_hash(policy),
            "e": policy.expires_at,
            "by": uuid.UUID(created_by),
        },
    )


def _policy(row: dict[str, Any]) -> g.GovernancePolicy:
    c = row["content"] if isinstance(row["content"], dict) else json.loads(row["content"])
    approval = str(row["approval_request_id"]) if row.get("approval_request_id") else None
    grants = tuple(
        g.GrantLease(
            grant_id=x["grant_id"],
            tool_id=x["tool_id"],
            action=x["action"],
            side_effect=x.get("side_effect", "NONE"),
            resource_kind=x["resource_kind"],
            resource_selector=x["resource_selector"],
            purpose=x["purpose"],
            region=x["region"],
            valid_until=datetime.fromisoformat(x["valid_until"]),
            approval_ref=approval,  # a lease is effective once the approved change that activated this version is on record
            data_class=x.get("data_class", "metadata"),
        )
        for x in c["leases"]
    )
    return g.GovernancePolicy(
        policy_id=str(row["policy_id"]),
        tenant_id=str(row["tenant_id"]),
        agent_id=str(row["agent_principal_id"]),
        version=int(row["version"]),
        risk_tier=row["risk_tier"],
        state=row["state"],
        region=row["region"],
        grants=grants,
        quotas=g.QuotaLimits.from_dict(c.get("quotas", {})),
        ai_enabled=bool(c.get("ai_enabled", False)),
        expires_at=row["expires_at"],
    )


async def load_policy(conn: AsyncConnection, policy_id: str) -> g.GovernancePolicy | None:
    row = (
        await conn.execute(
            text("SELECT * FROM agent.governance_policy WHERE policy_id = :p"),
            {"p": uuid.UUID(policy_id)},
        )
    ).first()
    return _policy(_row(row)) if row else None


async def active_policy(conn: AsyncConnection, agent_id: str) -> g.GovernancePolicy | None:
    row = (
        await conn.execute(
            text(
                "SELECT * FROM agent.governance_policy WHERE agent_principal_id = :a AND state = 'ACTIVE'"
            ),
            {"a": uuid.UUID(agent_id)},
        )
    ).first()
    return _policy(_row(row)) if row else None


async def list_policies(conn: AsyncConnection, agent_id: str) -> list[g.GovernancePolicy]:
    rows = (
        await conn.execute(
            text(
                "SELECT * FROM agent.governance_policy WHERE agent_principal_id = :a ORDER BY version DESC"
            ),
            {"a": uuid.UUID(agent_id)},
        )
    ).all()
    return [_policy(_row(r)) for r in rows]


async def set_policy_state(
    conn: AsyncConnection, policy_id: str, state: str, *, approval_request_id: str | None = None
) -> None:
    await conn.execute(
        text(
            "UPDATE agent.governance_policy SET state = :s, "
            "approval_request_id = coalesce(:r, approval_request_id) WHERE policy_id = :p"
        ),
        {"s": state, "r": _u(approval_request_id), "p": uuid.UUID(policy_id)},
    )


# ---------------------------------------------------------------- company baseline


async def load_baseline(
    conn: AsyncConnection, tenant_id: str
) -> tuple[g.CompanyBaseline, int] | None:
    row = (
        await conn.execute(
            text("SELECT * FROM agent.company_baseline WHERE tenant_id = :t"),
            {"t": uuid.UUID(tenant_id)},
        )
    ).first()
    if row is None:
        return None
    r = _row(row)
    quotas = r["quotas"] if isinstance(r["quotas"], dict) else json.loads(r["quotas"])
    return (
        g.CompanyBaseline(
            allowed_tools=frozenset(r["allowed_tools"]),
            allowed_regions=frozenset(r["allowed_regions"]),
            ai_providers=frozenset(r["ai_providers"]),
            quotas=g.QuotaLimits.from_dict(quotas),
            schedules_allowed=bool(r["schedules_allowed"]),
            frozen=bool(r["frozen"]),
        ),
        int(r["revision"]),
    )


async def save_baseline(
    conn: AsyncConnection, tenant_id: str, baseline: g.CompanyBaseline, *, actor: str
) -> int:
    row = (
        await conn.execute(
            text(
                "INSERT INTO agent.company_baseline (tenant_id, allowed_tools, allowed_regions, ai_providers, quotas, "
                "schedules_allowed, frozen, updated_by) VALUES (:t, :tools, :regions, :ai, CAST(:q AS jsonb), :s, :f, :by) "
                "ON CONFLICT (tenant_id) DO UPDATE SET allowed_tools = EXCLUDED.allowed_tools, "
                "allowed_regions = EXCLUDED.allowed_regions, ai_providers = EXCLUDED.ai_providers, quotas = EXCLUDED.quotas, "
                "schedules_allowed = EXCLUDED.schedules_allowed, revision = agent.company_baseline.revision + 1, "
                "updated_by = EXCLUDED.updated_by, updated_at = now() RETURNING revision"
            ),
            {
                "t": uuid.UUID(tenant_id),
                "tools": sorted(baseline.allowed_tools),
                "regions": sorted(baseline.allowed_regions),
                "ai": sorted(baseline.ai_providers),
                "q": _j(baseline.quotas.as_dict()),
                "s": baseline.schedules_allowed,
                "f": False,
                "by": uuid.UUID(actor),
            },
        )
    ).first()
    return int(row[0])  # type: ignore[index]


async def set_company_frozen(
    conn: AsyncConnection, tenant_id: str, frozen: bool, *, actor: str
) -> None:
    await conn.execute(
        text(
            "UPDATE agent.company_baseline SET frozen = :f, updated_by = :by, updated_at = now() WHERE tenant_id = :t"
        ),
        {"f": frozen, "by": uuid.UUID(actor), "t": uuid.UUID(tenant_id)},
    )


# ---------------------------------------------------------------- delegates (who holds which signer slot)


async def slots_of(conn: AsyncConnection, principal_id: str) -> frozenset[str]:
    rows = (
        await conn.execute(
            text(
                "SELECT slot FROM agent.governance_delegate WHERE principal_id = :p AND revoked_at IS NULL"
            ),
            {"p": uuid.UUID(principal_id)},
        )
    ).all()
    return frozenset(r[0] for r in rows)


async def slots_of_many(
    conn: AsyncConnection, principal_ids: Sequence[str]
) -> dict[str, frozenset[str]]:
    out: dict[str, set[str]] = {p: set() for p in principal_ids}
    if not principal_ids:
        return {}
    rows = (
        await conn.execute(
            text(
                "SELECT principal_id, slot FROM agent.governance_delegate "
                "WHERE principal_id = ANY(CAST(:p AS uuid[])) AND revoked_at IS NULL"
            ),
            {"p": [uuid.UUID(p) for p in principal_ids]},
        )
    ).all()
    for pid, slot in rows:
        out[str(pid)].add(slot)
    return {k: frozenset(v) for k, v in out.items()}


async def grant_slot(
    conn: AsyncConnection, tenant_id: str, principal_id: str, slot: str, *, by: str
) -> bool:
    row = (
        await conn.execute(
            text(
                "INSERT INTO agent.governance_delegate (delegate_id, tenant_id, principal_id, slot, granted_by) "
                "VALUES (:d, :t, :p, :s, :by) ON CONFLICT DO NOTHING RETURNING delegate_id"
            ),
            {
                "d": uuid.uuid4(),
                "t": uuid.UUID(tenant_id),
                "p": uuid.UUID(principal_id),
                "s": slot,
                "by": uuid.UUID(by),
            },
        )
    ).first()
    return row is not None


async def revoke_slot(conn: AsyncConnection, principal_id: str, slot: str, *, by: str) -> bool:
    row = (
        await conn.execute(
            text(
                "UPDATE agent.governance_delegate SET revoked_at = now(), revoked_by = :by "
                "WHERE principal_id = :p AND slot = :s AND revoked_at IS NULL RETURNING delegate_id"
            ),
            {"by": uuid.UUID(by), "p": uuid.UUID(principal_id), "s": slot},
        )
    ).first()
    return row is not None


async def list_delegates(conn: AsyncConnection) -> list[dict[str, Any]]:
    rows = (
        await conn.execute(
            text(
                "SELECT principal_id, slot, granted_by, granted_at FROM agent.governance_delegate "
                "WHERE revoked_at IS NULL ORDER BY slot, granted_at"
            )
        )
    ).all()
    return [
        {
            "principal_id": str(r[0]),
            "slot": r[1],
            "granted_by": str(r[2]),
            "granted_at": r[3].isoformat(),
        }
        for r in rows
    ]


# ---------------------------------------------------------------- change requests and signatures


async def insert_request(
    conn: AsyncConnection,
    tenant_id: str,
    req: g.ChangeRequest,
    *,
    subject: str,
    policy_id: str | None,
    delta: dict[str, Any],
    payload: dict[str, Any],
    reason_code: str,
    evidence_ref: str | None,
) -> int:
    row = (
        await conn.execute(
            text(
                "INSERT INTO agent.governance_change_request (request_id, tenant_id, agent_principal_id, subject, kind, requested_by, "
                "authors, policy_id, policy_hash, base_policy_hash, delta, delta_hash, required, payload, reason_code, evidence_ref, "
                "state, created_at, expires_at) VALUES (:id, :t, :a, :sub, :k, :by, CAST(:au AS uuid[]), :pol, :h, :bh, "
                "CAST(:d AS jsonb), :dh, :req, CAST(:pl AS jsonb), :rc, :ev, :st, :ca, :ex) RETURNING ref_no"
            ),
            {
                "id": uuid.UUID(req.request_id),
                "t": uuid.UUID(tenant_id),
                "a": uuid.UUID(req.agent_id),
                "sub": subject,
                "k": req.kind.value,
                "by": uuid.UUID(req.requested_by),
                "au": [uuid.UUID(x) for x in req.authors],
                "pol": _u(policy_id),
                "h": req.policy_hash,
                "bh": req.base_policy_hash,
                "d": _j(delta),
                "dh": req.delta_hash,
                "req": list(req.required),
                "pl": _j(payload),
                "rc": reason_code,
                "ev": evidence_ref,
                "st": req.state.value,
                "ca": req.created_at,
                "ex": req.expires_at,
            },
        )
    ).first()
    return int(row[0])  # type: ignore[index]


def _request(row: dict[str, Any], sigs: Sequence[g.Signature]) -> g.ChangeRequest:
    return g.ChangeRequest(
        request_id=str(row["request_id"]),
        tenant_id=str(row["tenant_id"]),
        agent_id=str(row["agent_principal_id"]),
        kind=g.ChangeKind(row["kind"]),
        requested_by=str(row["requested_by"]),
        authors=tuple(str(a) for a in row["authors"]),
        policy_hash=row["policy_hash"],
        base_policy_hash=row["base_policy_hash"],
        delta_hash=row["delta_hash"],
        required=tuple(row["required"]),
        created_at=row["created_at"],
        expires_at=row["expires_at"],
        state=g.RequestState(row["state"]),
        signatures=tuple(sigs),
    )


async def signatures_of(conn: AsyncConnection, request_id: str) -> list[g.Signature]:
    rows = (
        await conn.execute(
            text(
                "SELECT slot, signer, signed_at, policy_hash FROM agent.governance_signature "
                "WHERE request_id = :r ORDER BY signed_at"
            ),
            {"r": uuid.UUID(request_id)},
        )
    ).all()
    return [g.Signature(r[0], str(r[1]), r[2], r[3]) for r in rows]


async def load_request(
    conn: AsyncConnection, request_id: str, *, lock: bool = False
) -> tuple[g.ChangeRequest, dict[str, Any]] | None:
    sql = "SELECT * FROM agent.governance_change_request WHERE request_id = :r" + (
        " FOR UPDATE" if lock else ""
    )
    row = (await conn.execute(text(sql), {"r": uuid.UUID(request_id)})).first()
    if row is None:
        return None
    r = _row(row)
    return _request(r, await signatures_of(conn, request_id)), r


async def list_requests(
    conn: AsyncConnection,
    *,
    agent_id: str | None = None,
    states: Sequence[str] | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    rows = (
        await conn.execute(
            text(
                "SELECT * FROM agent.governance_change_request "
                "WHERE (CAST(:a AS uuid) IS NULL OR agent_principal_id = CAST(:a AS uuid)) "
                "AND (CAST(:st AS text[]) IS NULL OR state = ANY(CAST(:st AS text[]))) "
                "ORDER BY created_at DESC LIMIT :lim"
            ),
            {"a": _u(agent_id), "st": list(states) if states else None, "lim": limit},
        )
    ).all()
    out = []
    for row in rows:
        r = _row(row)
        out.append({**r, "_signatures": await signatures_of(conn, str(r["request_id"]))})
    return out


async def add_signature(
    conn: AsyncConnection, tenant_id: str, request_id: str, sig: g.Signature
) -> None:
    await conn.execute(
        text(
            "INSERT INTO agent.governance_signature (request_id, tenant_id, slot, signer, policy_hash, signed_at) "
            "VALUES (:r, :t, :s, :by, :h, :at)"
        ),
        {
            "r": uuid.UUID(request_id),
            "t": uuid.UUID(tenant_id),
            "s": sig.slot,
            "by": uuid.UUID(sig.signer),
            "h": sig.policy_hash,
            "at": sig.signed_at,
        },
    )


async def set_request_state(
    conn: AsyncConnection,
    request_id: str,
    state: g.RequestState,
    *,
    by: str | None = None,
    reason: str | None = None,
) -> None:
    await conn.execute(
        text(
            "UPDATE agent.governance_change_request SET state = :s, decided_by = coalesce(:by, decided_by), "
            "decided_reason = coalesce(:why, decided_reason) WHERE request_id = :r"
        ),
        {"s": state.value, "by": _u(by), "why": reason, "r": uuid.UUID(request_id)},
    )


async def expire_open_requests(conn: AsyncConnection, now: datetime) -> int:
    rows = (
        await conn.execute(
            text(
                "UPDATE agent.governance_change_request SET state = 'EXPIRED' "
                "WHERE state IN ('PENDING','CLARIFICATION','APPROVED') AND expires_at <= :n RETURNING request_id"
            ),
            {"n": now},
        )
    ).all()
    return len(rows)


# ---------------------------------------------------------------- budgets and reservations


async def lock_ceilings(conn: AsyncConnection, agent_id: str) -> list[dict[str, Any]]:
    """Every ACTIVE budget above a call by this agent (company, workspace, the agent's own, the per-call cap), row-locked in id
    order. Holding these locks is what makes the sum-then-insert below atomic against another call."""
    rows = (
        await conn.execute(
            text(
                "SELECT * FROM agent.budget WHERE state = 'ACTIVE' AND (scope IN ('COMPANY','WORKSPACE','CALL') "
                "OR agent_principal_id = :a) ORDER BY budget_id FOR UPDATE"
            ),
            {"a": uuid.UUID(agent_id)},
        )
    ).all()
    return [_row(r) for r in rows]


async def used_in_window(
    conn: AsyncConnection, *, agent_id: str | None, currency: str, window_start: datetime
) -> tuple[Decimal, Decimal]:
    """(committed, reserved) since `window_start`. A reservation counts at its upper bound until it settles, then at what
    was committed; adjustments (late invoices, refunds) are signed entries added on top, never edits."""
    args: dict[str, Any] = {"c": currency, "s": window_start, "a": _u(agent_id)}
    row = (
        await conn.execute(
            text(
                "SELECT coalesce(sum(CASE WHEN r.state = 'SETTLED' THEN r.committed ELSE 0 END), 0), "
                "coalesce(sum(CASE WHEN r.state = 'RESERVED' THEN r.upper_bound ELSE 0 END), 0) "
                "FROM agent.budget_reservation r WHERE r.currency = :c AND r.created_at >= :s "
                "AND (CAST(:a AS uuid) IS NULL OR r.agent_principal_id = CAST(:a AS uuid))"
            ),
            args,
        )
    ).first()
    adj = (
        await conn.execute(
            text(
                "SELECT coalesce(sum(a.amount), 0) FROM agent.budget_adjustment a "
                "WHERE a.currency = :c AND a.created_at >= :s "
                "AND (CAST(:a AS uuid) IS NULL OR a.agent_principal_id = CAST(:a AS uuid))"
            ),
            args,
        )
    ).first()
    return Decimal(row[0]) + Decimal(adj[0]), Decimal(row[1])  # type: ignore[index]


async def insert_budget(conn: AsyncConnection, tenant_id: str, b: dict[str, Any]) -> None:
    await conn.execute(
        text(
            "INSERT INTO agent.budget (budget_id, tenant_id, scope, agent_principal_id, currency, period, hard_cap, warn_pct_1, "
            "warn_pct_2, parent_id, revision, state, approval_request_id, effective_from, created_by) VALUES "
            "(:id, :t, :sc, :a, :cur, :per, :cap, :w1, :w2, :par, :rev, :st, :req, :eff, :by)"
        ),
        {
            "id": uuid.UUID(b["budget_id"]),
            "t": uuid.UUID(tenant_id),
            "sc": b["scope"],
            "a": _u(b.get("agent_id")),
            "cur": b["currency"],
            "per": b["period"],
            "cap": b["hard_cap"],
            "w1": b.get("warn_pct_1", 70),
            "w2": b.get("warn_pct_2", 90),
            "par": _u(b.get("parent_id")),
            "rev": b["revision"],
            "st": b["state"],
            "req": _u(b.get("approval_request_id")),
            "eff": b.get("effective_from"),
            "by": uuid.UUID(b["created_by"]),
        },
    )


async def list_budgets(
    conn: AsyncConnection,
    *,
    agent_id: str | None = None,
    states: Sequence[str] = ("ACTIVE", "PENDING"),
) -> list[dict[str, Any]]:
    args: dict[str, Any] = {"st": list(states)}
    cond = ""
    if agent_id:
        cond = "AND (scope <> 'AGENT' OR agent_principal_id = :a)"
        args["a"] = uuid.UUID(agent_id)
    rows = (
        await conn.execute(
            text(
                f"SELECT * FROM agent.budget WHERE state = ANY(:st) {cond} ORDER BY scope, revision DESC"
            ),
            args,
        )
    ).all()
    return [_row(r) for r in rows]


async def load_budget(
    conn: AsyncConnection, budget_id: str, *, lock: bool = False
) -> dict[str, Any] | None:
    row = (
        await conn.execute(
            text(
                "SELECT * FROM agent.budget WHERE budget_id = :b" + (" FOR UPDATE" if lock else "")
            ),
            {"b": uuid.UUID(budget_id)},
        )
    ).first()
    return _row(row) if row else None


async def set_budget_state(
    conn: AsyncConnection, budget_id: str, state: str, *, effective_from: datetime | None = None
) -> None:
    await conn.execute(
        text(
            "UPDATE agent.budget SET state = :s, effective_from = coalesce(:e, effective_from) WHERE budget_id = :b"
        ),
        {"s": state, "e": effective_from, "b": uuid.UUID(budget_id)},
    )


async def reservation_by_key(conn: AsyncConnection, key: str) -> dict[str, Any] | None:
    row = (
        await conn.execute(
            text("SELECT * FROM agent.budget_reservation WHERE idempotency_key = :k"), {"k": key}
        )
    ).first()
    return _row(row) if row else None


async def insert_reservation(conn: AsyncConnection, tenant_id: str, r: dict[str, Any]) -> None:
    await conn.execute(
        text(
            "INSERT INTO agent.budget_reservation (reservation_id, tenant_id, agent_principal_id, run_id, tool_id, spend_class, "
            "currency, upper_bound, state, idempotency_key, price_version, created_at, expires_at) VALUES "
            "(:id, :t, :a, :run, :tool, :cls, :cur, :ub, 'RESERVED', :k, :pv, :ca, :ex)"
        ),
        {
            "id": uuid.UUID(r["reservation_id"]),
            "t": uuid.UUID(tenant_id),
            "a": uuid.UUID(r["agent_id"]),
            "run": _u(r.get("run_id")),
            "tool": r["tool_id"],
            "cls": r["spend_class"],
            "cur": r["currency"],
            "ub": r["upper_bound"],
            "k": r["idempotency_key"],
            "pv": r["price_version"],
            "ca": r["created_at"],
            "ex": r["expires_at"],
        },
    )


async def load_reservation(
    conn: AsyncConnection, reservation_id: str, *, lock: bool = False
) -> dict[str, Any] | None:
    row = (
        await conn.execute(
            text(
                "SELECT * FROM agent.budget_reservation WHERE reservation_id = :r"
                + (" FOR UPDATE" if lock else "")
            ),
            {"r": uuid.UUID(reservation_id)},
        )
    ).first()
    return _row(row) if row else None


async def close_reservation(
    conn: AsyncConnection,
    reservation_id: str,
    *,
    state: str,
    committed: Decimal,
    released: Decimal,
    overrun: Decimal,
    at: datetime,
) -> None:
    await conn.execute(
        text(
            "UPDATE agent.budget_reservation SET state = :s, committed = :c, released = :r, overrun = :o, settled_at = :at "
            "WHERE reservation_id = :id"
        ),
        {
            "s": state,
            "c": committed,
            "r": released,
            "o": overrun,
            "at": at,
            "id": uuid.UUID(reservation_id),
        },
    )


async def stale_reservations(conn: AsyncConnection, now: datetime) -> list[dict[str, Any]]:
    rows = (
        await conn.execute(
            text(
                "SELECT * FROM agent.budget_reservation WHERE state = 'RESERVED' AND expires_at <= :n FOR UPDATE SKIP LOCKED"
            ),
            {"n": now},
        )
    ).all()
    return [_row(r) for r in rows]


async def open_reservations_for_run(conn: AsyncConnection, run_id: str) -> list[dict[str, Any]]:
    rows = (
        await conn.execute(
            text(
                "SELECT * FROM agent.budget_reservation WHERE run_id = :r AND state = 'RESERVED' FOR UPDATE"
            ),
            {"r": uuid.UUID(run_id)},
        )
    ).all()
    return [_row(r) for r in rows]


async def add_adjustment(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    reservation_id: str,
    agent_id: str,
    currency: str,
    amount: Decimal,
    reason: str,
    by: str,
) -> str:
    aid = str(uuid.uuid4())
    await conn.execute(
        text(
            "INSERT INTO agent.budget_adjustment (adjustment_id, tenant_id, reservation_id, agent_principal_id, currency, amount, "
            "reason, created_by) VALUES (:id, :t, :r, :a, :c, :amt, :why, :by)"
        ),
        {
            "id": uuid.UUID(aid),
            "t": uuid.UUID(tenant_id),
            "r": uuid.UUID(reservation_id),
            "a": uuid.UUID(agent_id),
            "c": currency,
            "amt": amount,
            "why": reason,
            "by": uuid.UUID(by),
        },
    )
    return aid


# ---------------------------------------------------------------- runs and usage counters


async def usage_snapshot(
    conn: AsyncConnection, agent_id: str, day_start: datetime
) -> g.UsageSnapshot:
    row = (
        await conn.execute(
            text(
                "SELECT "
                " count(*) FILTER (WHERE state IN ('QUEUED','RUNNING') AND agent_principal_id = :a), "
                " count(DISTINCT agent_principal_id) FILTER (WHERE state IN ('QUEUED','RUNNING') AND agent_principal_id <> :a), "
                " count(*) FILTER (WHERE started_at >= :d AND agent_principal_id = :a), "
                " count(*) FILTER (WHERE started_at >= :d), "
                " coalesce(sum(records) FILTER (WHERE started_at >= :d), 0) "
                "FROM agent.run_ledger"
            ),
            {"a": uuid.UUID(agent_id), "d": day_start},
        )
    ).first()
    return g.UsageSnapshot(
        active_runs_agent=int(row[0]),  # type: ignore[index]
        running_agents_tenant=int(
            row[1]
        ),  # other agents; the caller adds itself when it is not yet running
        runs_agent_today=int(row[2]),
        runs_tenant_today=int(row[3]),
        records_tenant_today=int(row[4]),
    )


async def lock_agent_runs(conn: AsyncConnection, agent_id: str) -> None:
    """Serialize run starts for one agent so two starts cannot both pass the concurrency check."""
    await conn.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
        {"k": f"agent-run:{agent_id}"},
    )
    await conn.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 1))"), {"k": "agent-run-tenant"}
    )


async def run_by_key(conn: AsyncConnection, key: str) -> dict[str, Any] | None:
    row = (
        await conn.execute(
            text("SELECT * FROM agent.run_ledger WHERE idempotency_key = :k"), {"k": key}
        )
    ).first()
    return _row(row) if row else None


async def insert_run(conn: AsyncConnection, tenant_id: str, r: dict[str, Any]) -> None:
    await conn.execute(
        text(
            "INSERT INTO agent.run_ledger (run_id, tenant_id, agent_principal_id, state, mode, queued_at, idempotency_key, policy_hash, "
            "started_at, deadline_at) VALUES (:id, :t, :a, :state, :mode, :q, :k, :h, :s, :d)"
        ),
        {
            "id": uuid.UUID(r["run_id"]),
            "t": uuid.UUID(tenant_id),
            "a": uuid.UUID(r["agent_id"]),
            "state": r.get("state", "RUNNING"),
            "mode": r.get("mode", "MANUAL"),
            "q": r["started_at"] if r.get("state") == "QUEUED" else None,
            "k": r["idempotency_key"],
            "h": r["policy_hash"],
            "s": r["started_at"],
            "d": r["deadline_at"],
        },
    )


async def load_run(
    conn: AsyncConnection, run_id: str, *, lock: bool = False
) -> dict[str, Any] | None:
    row = (
        await conn.execute(
            text(
                "SELECT * FROM agent.run_ledger WHERE run_id = :r" + (" FOR UPDATE" if lock else "")
            ),
            {"r": uuid.UUID(run_id)},
        )
    ).first()
    return _row(row) if row else None


async def add_run_records(conn: AsyncConnection, run_id: str, records: int, proposals: int) -> None:
    await conn.execute(
        text(
            "UPDATE agent.run_ledger SET records = records + :r, proposals = proposals + :p WHERE run_id = :id"
        ),
        {"r": records, "p": proposals, "id": uuid.UUID(run_id)},
    )


async def finish_run(
    conn: AsyncConnection, run_id: str, *, state: str, at: datetime, reason: str | None
) -> bool:
    row = (
        await conn.execute(
            text(
                "UPDATE agent.run_ledger SET state = :s, ended_at = :at, reason_code = :why "
                "WHERE run_id = :id AND state IN ('QUEUED','RUNNING') RETURNING run_id"
            ),
            {"s": state, "at": at, "why": reason, "id": uuid.UUID(run_id)},
        )
    ).first()
    return row is not None


async def queued_runs(conn: AsyncConnection, limit: int) -> list[dict[str, Any]]:
    """The oldest queued runs across every company, each row locked so two workers never take the same one (worker pass, BYPASSRLS)."""
    rows = (
        await conn.execute(
            text(
                "SELECT * FROM agent.run_ledger WHERE state = 'QUEUED' ORDER BY queued_at LIMIT :n FOR UPDATE SKIP LOCKED"
            ),
            {"n": limit},
        )
    ).all()
    return [_row(r) for r in rows]


async def mark_running(
    conn: AsyncConnection, run_id: str, *, now: datetime, deadline: datetime
) -> bool:
    """QUEUED to RUNNING, once. The deadline starts counting here, not when the run was queued."""
    row = (
        await conn.execute(
            text(
                "UPDATE agent.run_ledger SET state = 'RUNNING', started_at = :now, deadline_at = :d "
                "WHERE run_id = :id AND state = 'QUEUED' RETURNING run_id"
            ),
            {"now": now, "d": deadline, "id": uuid.UUID(run_id)},
        )
    ).first()
    return row is not None


async def overdue_runs(conn: AsyncConnection, now: datetime) -> list[dict[str, Any]]:
    rows = (
        await conn.execute(
            text(
                "SELECT * FROM agent.run_ledger WHERE state = 'RUNNING' AND deadline_at <= :n FOR UPDATE SKIP LOCKED"
            ),
            {"n": now},
        )
    ).all()
    return [_row(r) for r in rows]


async def running_runs(conn: AsyncConnection, agent_id: str | None = None) -> list[dict[str, Any]]:
    cond = "AND agent_principal_id = :a" if agent_id else ""
    args = {"a": uuid.UUID(agent_id)} if agent_id else {}
    rows = (
        await conn.execute(
            text(f"SELECT * FROM agent.run_ledger WHERE state IN ('QUEUED','RUNNING') {cond}"), args
        )
    ).all()
    return [_row(r) for r in rows]


# ---------------------------------------------------------------- evidence stream (append-only, hash-chained)


def event_digest(prev: str, e: dict[str, Any]) -> str:
    return g.digest({"prev": prev, **e})


async def append_event(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    agent_id: str | None,
    run_id: str | None,
    actor: str | None,
    actor_type: str,
    action: str,
    outcome: str,
    code: str | None,
    tool_id: str | None,
    resource: str | None,
    region: str | None,
    policy_hash: str | None,
    correlation_id: str,
    cost_receipt: dict[str, Any] | None,
    occurred_at: datetime,
) -> str:
    """One immutable row, chained by digest to the previous row of ITS OWN chain (one chain per agent, one for company-level rows), so a
    deleted or edited row breaks every digest after it in that chain. The advisory lock makes the previous-digest read and this insert one
    step per chain, so two agents never wait for each other."""
    chain = agent_id or "tenant"
    await conn.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 2))"),
        {"k": f"gov-events:{tenant_id}:{chain}"},
    )
    prev = (
        await conn.execute(
            text(
                "SELECT digest FROM agent.governance_event WHERE chain_key = :c ORDER BY seq DESC LIMIT 1"
            ),
            {"c": chain},
        )
    ).first()
    prev_digest = prev[0] if prev else "sha256:" + "0" * 64
    event_id = str(uuid.uuid4())
    facts = {
        "event_id": event_id,
        "tenant_id": tenant_id,
        "agent_id": agent_id,
        "run_id": run_id,
        "actor": actor,
        "actor_type": actor_type,
        "action": action,
        "outcome": outcome,
        "code": code,
        "tool_id": tool_id,
        "resource": resource,
        "region": region,
        "policy_hash": policy_hash,
        "correlation_id": correlation_id,
        "cost_receipt": cost_receipt,
        "occurred_at": occurred_at.isoformat(),
    }
    digest = event_digest(prev_digest, facts)
    await conn.execute(
        text(
            "INSERT INTO agent.governance_event (event_id, tenant_id, agent_principal_id, run_id, actor, actor_type, action, outcome, "
            "code, tool_id, resource, region, policy_hash, correlation_id, cost_receipt, occurred_at, prev_digest, digest) VALUES "
            "(:e, :t, :a, :run, :act, :at, :ac, :o, :c, :tool, :res, :reg, :ph, :cor, CAST(:cost AS jsonb), :occ, :prev, :dig)"
        ),
        {
            "e": uuid.UUID(event_id),
            "t": uuid.UUID(tenant_id),
            "a": _u(agent_id),
            "run": _u(run_id),
            "act": _u(actor),
            "at": actor_type,
            "ac": action,
            "o": outcome,
            "c": code,
            "tool": tool_id,
            "res": resource,
            "reg": region,
            "ph": policy_hash,
            "cor": correlation_id,
            "cost": _j(cost_receipt) if cost_receipt is not None else None,
            "occ": occurred_at,
            "prev": prev_digest,
            "dig": digest,
        },
    )
    return event_id


async def list_events(
    conn: AsyncConnection,
    *,
    agent_id: str | None,
    since: datetime | None,
    until: datetime | None,
    before_seq: int | None,
    outcome: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    rows = (
        await conn.execute(
            text(
                "SELECT * FROM agent.governance_event "
                "WHERE (CAST(:a AS uuid) IS NULL OR agent_principal_id = CAST(:a AS uuid)) "
                "AND (CAST(:since AS timestamptz) IS NULL OR occurred_at >= CAST(:since AS timestamptz)) "
                "AND (CAST(:until AS timestamptz) IS NULL OR occurred_at < CAST(:until AS timestamptz)) "
                "AND (CAST(:bs AS bigint) IS NULL OR seq < CAST(:bs AS bigint)) "
                "AND (CAST(:o AS text) IS NULL OR outcome = CAST(:o AS text)) "
                "ORDER BY seq DESC LIMIT :lim"
            ),
            {
                "a": _u(agent_id),
                "since": since,
                "until": until,
                "bs": before_seq,
                "o": outcome,
                "lim": limit,
            },
        )
    ).all()
    return [_row(r) for r in rows]


async def verify_chain(conn: AsyncConnection, *, limit: int = 5000) -> dict[str, Any]:
    """Recompute every digest of every chain from the oldest row we hold. A mismatch names the first broken sequence."""
    rows = (
        await conn.execute(
            text("SELECT * FROM agent.governance_event ORDER BY seq ASC LIMIT :n"), {"n": limit}
        )
    ).all()
    previous: dict[str, str] = {}
    for r in map(_row, rows):
        prev = previous.get(r["chain_key"])
        if prev is not None and r["prev_digest"] != prev:
            return {"intact": False, "broken_at": r["seq"], "checked": len(rows)}
        facts = {
            "event_id": str(r["event_id"]),
            "tenant_id": str(r["tenant_id"]),
            "agent_id": str(r["agent_principal_id"]) if r["agent_principal_id"] else None,
            "run_id": str(r["run_id"]) if r["run_id"] else None,
            "actor": str(r["actor"]) if r["actor"] else None,
            "actor_type": r["actor_type"],
            "action": r["action"],
            "outcome": r["outcome"],
            "code": r["code"],
            "tool_id": r["tool_id"],
            "resource": r["resource"],
            "region": r["region"],
            "policy_hash": r["policy_hash"],
            "correlation_id": r["correlation_id"],
            "cost_receipt": r["cost_receipt"]
            if r["cost_receipt"] is None or isinstance(r["cost_receipt"], dict)
            else json.loads(r["cost_receipt"]),
            "occurred_at": r["occurred_at"].isoformat(),
        }
        if event_digest(r["prev_digest"], facts) != r["digest"]:
            return {"intact": False, "broken_at": r["seq"], "checked": len(rows)}
        previous[r["chain_key"]] = r["digest"]
    return {"intact": True, "broken_at": None, "checked": len(rows), "chains": len(previous)}


async def counts_by_outcome(
    conn: AsyncConnection, *, since: datetime, agent_id: str | None = None
) -> dict[str, int]:
    cond = "AND agent_principal_id = :a" if agent_id else ""
    args: dict[str, Any] = {"s": since}
    if agent_id:
        args["a"] = uuid.UUID(agent_id)
    rows = (
        await conn.execute(
            text(
                f"SELECT outcome, count(*) FROM agent.governance_event WHERE occurred_at >= :s {cond} GROUP BY outcome"
            ),
            args,
        )
    ).all()
    return {r[0]: int(r[1]) for r in rows}


async def last_event_at(conn: AsyncConnection, agent_id: str | None = None) -> datetime | None:
    cond = "WHERE agent_principal_id = :a" if agent_id else ""
    args = {"a": uuid.UUID(agent_id)} if agent_id else {}
    row = (
        await conn.execute(
            text(f"SELECT max(occurred_at) FROM agent.governance_event {cond}"), args
        )
    ).first()
    return row[0] if row else None


# ---------------------------------------------------------------- freezes


async def insert_freeze(conn: AsyncConnection, tenant_id: str, f: dict[str, Any]) -> int:
    row = (
        await conn.execute(
            text(
                "INSERT INTO agent.freeze_control (case_id, tenant_id, scope, agent_principal_id, initiator, reason, severity, issued_at) "
                "VALUES (:c, :t, :s, :a, :i, :r, :sev, :at) RETURNING generation"
            ),
            {
                "c": uuid.UUID(f["case_id"]),
                "t": uuid.UUID(tenant_id),
                "s": f["scope"],
                "a": _u(f.get("agent_id")),
                "i": uuid.UUID(f["initiator"]),
                "r": f["reason"],
                "sev": f.get("severity", "P1"),
                "at": f["issued_at"],
            },
        )
    ).first()
    return int(row[0])  # type: ignore[index]


def _freeze(r: dict[str, Any]) -> g.Freeze:
    return g.Freeze(
        case_id=str(r["case_id"]),
        scope=r["scope"],
        agent_id=str(r["agent_principal_id"]) if r["agent_principal_id"] else None,
        generation=int(r["generation"]),
        initiator=str(r["initiator"]),
        reason=r["reason"],
        issued_at=r["issued_at"],
        cleared_by=tuple(str(x) for x in r["restored_by"]),
        restored_at=r["restored_at"],
    )


async def active_freezes(conn: AsyncConnection) -> list[g.Freeze]:
    rows = (
        await conn.execute(text("SELECT * FROM agent.freeze_control WHERE restored_at IS NULL"))
    ).all()
    return [_freeze(_row(r)) for r in rows]


async def list_freezes(conn: AsyncConnection, *, limit: int = 50) -> list[dict[str, Any]]:
    rows = (
        await conn.execute(
            text("SELECT * FROM agent.freeze_control ORDER BY generation DESC LIMIT :n"),
            {"n": limit},
        )
    ).all()
    return [_row(r) for r in rows]


async def load_freeze(
    conn: AsyncConnection, case_id: str, *, lock: bool = False
) -> g.Freeze | None:
    row = (
        await conn.execute(
            text(
                "SELECT * FROM agent.freeze_control WHERE case_id = :c"
                + (" FOR UPDATE" if lock else "")
            ),
            {"c": uuid.UUID(case_id)},
        )
    ).first()
    return _freeze(_row(row)) if row else None


async def attach_restore_request(conn: AsyncConnection, case_id: str, request_id: str) -> None:
    await conn.execute(
        text(
            "UPDATE agent.freeze_control SET restore_request_id = :r WHERE case_id = :c AND restored_at IS NULL"
        ),
        {"r": uuid.UUID(request_id), "c": uuid.UUID(case_id)},
    )


async def restore_freeze(
    conn: AsyncConnection, case_id: str, *, by: Sequence[str], at: datetime
) -> None:
    await conn.execute(
        text(
            "UPDATE agent.freeze_control SET restored_at = :at, restored_by = CAST(:by AS uuid[]) "
            "WHERE case_id = :c AND restored_at IS NULL"
        ),
        {"at": at, "by": [uuid.UUID(x) for x in by], "c": uuid.UUID(case_id)},
    )
