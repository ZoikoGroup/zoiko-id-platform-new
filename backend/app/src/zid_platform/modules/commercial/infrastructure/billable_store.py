"""The billable ledger in PostgreSQL (`commercial.billable_event` and friends; CR-025).

Every function runs on the caller's connection and RLS scope. Inserts use ON CONFLICT DO NOTHING on the
(tenant, deduplication key) so a second record of the same operation changes nothing.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.commercial.domain.billable import (
    BillableEvent,
    hash_total,
)


async def insert_event(conn: AsyncConnection, e: BillableEvent) -> bool:
    """True when this call created the row; False when the operation was already recorded."""
    r = await conn.execute(
        text(
            "INSERT INTO commercial.billable_event (event_id, tenant_id, deduplication_key, meter_id, quantity, "
            "occurred_at, window_month, late, recovered, environment_id, principal_class, region, "
            "source_request_id, row_hash, legal_payer_id, origin, partner_id, transaction_type, billable_result, finality, "
            "market_policy, supplier_rate_version) "
            "VALUES (:i, :t, :k, :m, :q, :o, :w, :l, :r, :e, :p, :g, :s, :h, :lp, :or_, :pt, :tt, :br, :fi, :mp, :sv) "
            "ON CONFLICT (tenant_id, deduplication_key) DO NOTHING"
        ),
        {
            "i": uuid.UUID(e.event_id),
            "t": uuid.UUID(e.tenant_id),
            "k": e.deduplication_key,
            "m": e.meter_id,
            "q": e.quantity,
            "o": e.occurred_at,
            "w": e.window_month,
            "l": e.late,
            "r": e.recovered,
            "e": e.environment_id,
            "p": e.principal_class,
            "g": e.region,
            "s": e.source_request_id,
            "h": e.row_hash,
            "lp": uuid.UUID(e.legal_payer_id) if e.legal_payer_id else None,
            "or_": e.origin,
            "pt": uuid.UUID(e.partner_id) if e.partner_id else None,
            "tt": e.transaction_type,
            "br": e.billable_result,
            "fi": e.finality,
            "mp": e.market_policy,
            "sv": e.supplier_rate_version,
        },
    )
    return bool(r.rowcount)


async def frozen_windows(conn: AsyncConnection, month: date) -> set[str]:
    rows = (
        await conn.execute(
            text("SELECT meter_id FROM commercial.meter_window WHERE window_month = :w"),
            {"w": month},
        )
    ).all()
    return {r[0] for r in rows}


async def canonical_principal(conn: AsyncConnection, tenant_id: str, principal_id: str) -> str:
    """The surviving principal after any recorded merges (a chain A -> B -> C resolves to C)."""
    current = principal_id
    for _ in range(32):  # merges are shallow; the bound only guards against a corrupt loop
        nxt = (
            await conn.execute(
                text(
                    "SELECT into_principal_id FROM commercial.mah_merge "
                    "WHERE tenant_id = :t AND from_principal_id = :p"
                ),
                {"t": uuid.UUID(tenant_id), "p": uuid.UUID(current)},
            )
        ).scalar_one_or_none()
        if nxt is None:
            return current
        current = str(nxt)
    raise RuntimeError("MAH merge chain too long")


async def mark_mah(conn: AsyncConnection, tenant_id: str, month: date, principal_id: str) -> bool:
    """True the first time this person is active in this tenant this month. A principal that was merged into
    another is counted as the surviving one."""
    principal_id = await canonical_principal(conn, tenant_id, principal_id)
    seen = (
        await conn.execute(
            text(
                "WITH RECURSIVE members(pid) AS ("
                " SELECT CAST(:p AS uuid) UNION"
                " SELECT m.from_principal_id FROM commercial.mah_merge m"
                " JOIN members x ON m.into_principal_id = x.pid WHERE m.tenant_id = :t) "
                "SELECT 1 FROM commercial.mah_activity WHERE tenant_id = :t AND window_month = :w "
                "AND principal_id IN (SELECT pid FROM members) LIMIT 1"
            ),
            {"t": uuid.UUID(tenant_id), "w": month, "p": uuid.UUID(principal_id)},
        )
    ).first()
    if seen is not None:  # this person, under any merged identity, was already counted this month
        return False
    r = await conn.execute(
        text(
            "INSERT INTO commercial.mah_activity (tenant_id, window_month, principal_id) "
            "VALUES (:t, :w, :p) ON CONFLICT DO NOTHING"
        ),
        {"t": uuid.UUID(tenant_id), "w": month, "p": uuid.UUID(principal_id)},
    )
    return bool(r.rowcount)


async def insert_merge(
    conn: AsyncConnection, tenant_id: str, from_id: str, into_id: str, at: datetime
) -> bool:
    """Record that `from_id` was merged into `into_id`. False when this merge is already recorded."""
    r = await conn.execute(
        text(
            "INSERT INTO commercial.mah_merge (tenant_id, from_principal_id, into_principal_id, merged_at) "
            "VALUES (:t, :f, :i, :a) ON CONFLICT DO NOTHING"
        ),
        {"t": uuid.UUID(tenant_id), "f": uuid.UUID(from_id), "i": uuid.UUID(into_id), "a": at},
    )
    return bool(r.rowcount)


async def double_counted_months(
    conn: AsyncConnection, tenant_id: str, from_id: str, into_id: str
) -> list[date]:
    """Months in which both identities of a merged person were already counted before the merge was known."""
    rows = (
        await conn.execute(
            text(
                "SELECT a.window_month FROM commercial.mah_activity a "
                "JOIN commercial.mah_activity b USING (tenant_id, window_month) "
                "WHERE a.tenant_id = :t AND a.principal_id = :f AND b.principal_id = :i "
                "ORDER BY a.window_month"
            ),
            {"t": uuid.UUID(tenant_id), "f": uuid.UUID(from_id), "i": uuid.UUID(into_id)},
        )
    ).scalars()
    return list(rows)


async def window_events(
    conn: AsyncConnection, month: date, meter: str | None = None
) -> list[dict[str, Any]]:
    rows = (
        await conn.execute(
            text(
                "SELECT event_id, deduplication_key, meter_id, quantity, occurred_at, late, recovered, row_hash "
                "FROM commercial.billable_event WHERE window_month = :w AND (CAST(:m AS text) IS NULL OR meter_id = :m) "
                "ORDER BY occurred_at, event_id"
            ),
            {"w": month, "m": meter},
        )
    ).mappings()
    return [dict(r) for r in rows]


async def event_totals(conn: AsyncConnection, month: date) -> dict[str, tuple[int, Decimal]]:
    rows = (
        await conn.execute(
            text(
                "SELECT meter_id, count(*), sum(quantity) FROM commercial.billable_event "
                "WHERE window_month = :w GROUP BY meter_id"
            ),
            {"w": month},
        )
    ).all()
    return {r[0]: (int(r[1]), Decimal(r[2])) for r in rows}


async def adjustment_totals(conn: AsyncConnection, month: date) -> dict[str, Decimal]:
    rows = (
        await conn.execute(
            text(
                "SELECT meter_id, sum(quantity_delta) FROM commercial.billable_adjustment "
                "WHERE window_month = :w AND state = 'APPROVED' GROUP BY meter_id"
            ),
            {"w": month},
        )
    ).all()
    return {r[0]: Decimal(r[1]) for r in rows}


async def freeze(conn: AsyncConnection, tenant_id: str, month: date, meter: str) -> dict[str, Any]:
    """Freeze one meter for one month: count, quantity and an order-independent hash of its rows."""
    rows = (
        await conn.execute(
            text(
                "SELECT row_hash, quantity FROM commercial.billable_event "
                "WHERE window_month = :w AND meter_id = :m FOR SHARE"
            ),
            {"w": month, "m": meter},
        )
    ).all()
    total = hash_total(r[0] for r in rows)
    quantity = sum((Decimal(r[1]) for r in rows), Decimal(0))
    await conn.execute(
        text(
            "INSERT INTO commercial.meter_window (tenant_id, meter_id, window_month, event_count, "
            "quantity_total, hash_total) VALUES (:t, :m, :w, :c, :q, :h)"
        ),
        {
            "t": uuid.UUID(tenant_id),
            "m": meter,
            "w": month,
            "c": len(rows),
            "q": quantity,
            "h": total,
        },
    )
    return {
        "meter": meter,
        "month": month.isoformat(),
        "event_count": len(rows),
        "quantity": quantity,
        "hash_total": total,
    }


async def windows(conn: AsyncConnection, month: date) -> list[dict[str, Any]]:
    rows = (
        await conn.execute(
            text(
                "SELECT meter_id, window_month, frozen_at, event_count, quantity_total, hash_total "
                "FROM commercial.meter_window WHERE window_month = :w ORDER BY meter_id"
            ),
            {"w": month},
        )
    ).mappings()
    return [dict(r) for r in rows]


async def request_adjustment(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    event_id: str,
    meter_id: str,
    window_month: date,
    quantity_delta: Decimal,
    reason_code: str,
    requested_by: str,
) -> str:
    aid = uuid.uuid4()
    await conn.execute(
        text(
            "INSERT INTO commercial.billable_adjustment (adjustment_id, tenant_id, adjusts_event_id, meter_id, "
            "window_month, quantity_delta, reason_code, requested_by) VALUES (:i, :t, :e, :m, :w, :q, :r, :b)"
        ),
        {
            "i": aid,
            "t": uuid.UUID(tenant_id),
            "e": uuid.UUID(event_id),
            "m": meter_id,
            "w": window_month,
            "q": quantity_delta,
            "r": reason_code,
            "b": uuid.UUID(requested_by),
        },
    )
    return str(aid)


async def load_event(conn: AsyncConnection, event_id: str) -> dict[str, Any] | None:
    row = (
        (
            await conn.execute(
                text(
                    "SELECT event_id, meter_id, window_month FROM commercial.billable_event WHERE event_id = :i"
                ),
                {"i": uuid.UUID(event_id)},
            )
        )
        .mappings()
        .first()
    )
    return dict(row) if row else None


async def load_adjustment(conn: AsyncConnection, adjustment_id: str) -> dict[str, Any] | None:
    row = (
        (
            await conn.execute(
                text(
                    "SELECT * FROM commercial.billable_adjustment WHERE adjustment_id = :i FOR UPDATE"
                ),
                {"i": uuid.UUID(adjustment_id)},
            )
        )
        .mappings()
        .first()
    )
    return dict(row) if row else None


async def decide_adjustment(
    conn: AsyncConnection, adjustment_id: str, *, approver: str, approve: bool, now: datetime
) -> None:
    await conn.execute(
        text(
            "UPDATE commercial.billable_adjustment SET state = :s, approved_by = :a, decided_at = :n "
            "WHERE adjustment_id = :i"
        ),
        {
            "s": "APPROVED" if approve else "REJECTED",
            "a": uuid.UUID(approver),
            "n": now,
            "i": uuid.UUID(adjustment_id),
        },
    )
