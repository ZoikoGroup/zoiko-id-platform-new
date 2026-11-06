"""Catalogue, price books and entitlements in PostgreSQL (schema `commercial`, owner: commercial; CR-024).

The catalogue tables are platform data (no RLS); `entitlement` and `entitlement_override` are per tenant
under forced RLS. Money is `Decimal`, never float.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.commercial.domain.catalogue import (
    CommercialError,
    Override,
    PlanCapability,
)


def _dec(v: Decimal | None) -> str | None:
    return None if v is None else format(v.normalize(), "f")


async def plans(conn: AsyncConnection) -> list[dict[str, Any]]:
    rows = (
        await conn.execute(
            text("SELECT * FROM commercial.plan WHERE published ORDER BY sort_order")
        )
    ).mappings()
    return [dict(r) for r in rows]


async def plan(conn: AsyncConnection, plan_code: str) -> dict[str, Any] | None:
    row = (
        (
            await conn.execute(
                text("SELECT * FROM commercial.plan WHERE plan_code = :p"), {"p": plan_code}
            )
        )
        .mappings()
        .first()
    )
    return dict(row) if row else None


async def plan_capabilities(conn: AsyncConnection, plan_code: str) -> list[PlanCapability]:
    rows = (
        await conn.execute(
            text(
                "SELECT pc.capability_key, pc.mode, pc.tier, pc.included_count, c.released "
                "FROM commercial.plan_capability pc JOIN commercial.capability c USING (capability_key) "
                "WHERE pc.plan_code = :p ORDER BY pc.capability_key"
            ),
            {"p": plan_code},
        )
    ).all()
    return [PlanCapability(r[0], r[1], r[2], r[3], bool(r[4])) for r in rows]


async def plan_capability(conn: AsyncConnection, plan_code: str, key: str) -> PlanCapability | None:
    row = (
        await conn.execute(
            text(
                "SELECT pc.capability_key, pc.mode, pc.tier, pc.included_count, c.released "
                "FROM commercial.plan_capability pc JOIN commercial.capability c USING (capability_key) "
                "WHERE pc.plan_code = :p AND pc.capability_key = :k"
            ),
            {"p": plan_code, "k": key},
        )
    ).first()
    return PlanCapability(row[0], row[1], row[2], row[3], bool(row[4])) if row else None


async def active_entitlement(conn: AsyncConnection) -> dict[str, Any] | None:
    """The tenant's ACTIVE or SUSPENDED entitlement (RLS scopes the tenant), or None: the default plan applies."""
    row = (
        (
            await conn.execute(
                text(
                    "SELECT * FROM commercial.entitlement WHERE state IN ('ACTIVE', 'SUSPENDED') "
                    "ORDER BY effective_from DESC LIMIT 1"
                )
            )
        )
        .mappings()
        .first()
    )
    return dict(row) if row else None


async def entitlement_history(conn: AsyncConnection) -> list[dict[str, Any]]:
    rows = (
        await conn.execute(
            text(
                "SELECT * FROM commercial.entitlement ORDER BY effective_from DESC, created_at DESC"
            )
        )
    ).mappings()
    return [dict(r) for r in rows]


async def overrides(conn: AsyncConnection, now: datetime) -> list[Override]:
    rows = (
        await conn.execute(
            text(
                "SELECT capability_key, grant_mode, extra_count, expires_at, revoked_at "
                "FROM commercial.entitlement_override "
                "WHERE revoked_at IS NULL AND (expires_at IS NULL OR expires_at > :n)"
            ),
            {"n": now},
        )
    ).all()
    return [Override(r[0], r[1], r[2], r[3], r[4] is not None) for r in rows]


async def set_entitlement(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    plan_code: str,
    price_book_id: str | None,
    actor: str | None,
    now: datetime,
    source: str = "OPERATOR",
    source_order_id: str | None = None,
) -> dict[str, Any]:
    """End the current entitlement (history stays) and start the new one from `now`; never retroactive."""
    tid = uuid.UUID(tenant_id)
    prior = (
        (
            await conn.execute(
                text(
                    "SELECT entitlement_id, version, plan_code, effective_from FROM commercial.entitlement "
                    "WHERE tenant_id = :t AND state IN ('ACTIVE', 'SUSPENDED') FOR UPDATE"
                ),
                {"t": tid},
            )
        )
        .mappings()
        .first()
    )
    version = 1
    if prior is not None:
        if prior["effective_from"] >= now:
            raise CommercialError("ENTITLEMENT_EFFECTIVE_DATE", "a change cannot be backdated")
        version = int(prior["version"]) + 1
        await conn.execute(
            text(
                "UPDATE commercial.entitlement SET state = 'ENDED', effective_to = :n "
                "WHERE entitlement_id = :i"
            ),
            {"n": now, "i": prior["entitlement_id"]},
        )
    new_id = uuid.uuid4()
    await conn.execute(
        text(
            "INSERT INTO commercial.entitlement (entitlement_id, tenant_id, plan_code, price_book_id, state, "
            "effective_from, source, source_order_id, version, created_by) "
            "VALUES (:i, :t, :p, :b, 'ACTIVE', :n, :s, :o, :v, :a)"
        ),
        {
            "i": new_id,
            "t": tid,
            "p": plan_code,
            "b": uuid.UUID(price_book_id) if price_book_id else None,
            "n": now,
            "s": source,
            "o": uuid.UUID(source_order_id) if source_order_id else None,
            "v": version,
            "a": uuid.UUID(actor) if actor else None,
        },
    )
    return {
        "entitlement_id": str(new_id),
        "plan_code": plan_code,
        "version": version,
        "previous_plan": prior["plan_code"] if prior else None,
    }


async def set_state(conn: AsyncConnection, state: str) -> bool:
    """ACTIVE <-> SUSPENDED for the tenant's current entitlement."""
    r = await conn.execute(
        text(
            "UPDATE commercial.entitlement SET state = :s WHERE state IN ('ACTIVE', 'SUSPENDED') "
            "AND state <> :s"
        ),
        {"s": state},
    )
    return bool(r.rowcount)


async def add_override(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    capability_key: str,
    grant_mode: str | None,
    extra_count: int | None,
    reason: str,
    approved_by: str,
    created_by: str,
    expires_at: datetime | None,
) -> str:
    oid = uuid.uuid4()
    await conn.execute(
        text(
            "INSERT INTO commercial.entitlement_override (override_id, tenant_id, capability_key, grant_mode, "
            "extra_count, reason, approved_by, created_by, expires_at) "
            "VALUES (:i, :t, :k, :g, :x, :r, :a, :c, :e)"
        ),
        {
            "i": oid,
            "t": uuid.UUID(tenant_id),
            "k": capability_key,
            "g": grant_mode,
            "x": extra_count,
            "r": reason,
            "a": uuid.UUID(approved_by),
            "c": uuid.UUID(created_by),
            "e": expires_at,
        },
    )
    return str(oid)


# ------------------------------------------------------------------------------- price books


async def approved_price_book(conn: AsyncConnection, now: datetime) -> dict[str, Any] | None:
    row = (
        (
            await conn.execute(
                text(
                    "SELECT * FROM commercial.price_book WHERE status = 'APPROVED' AND effective_from <= :n "
                    "ORDER BY version DESC LIMIT 1"
                ),
                {"n": now},
            )
        )
        .mappings()
        .first()
    )
    return dict(row) if row else None


async def price_book(conn: AsyncConnection, price_book_id: str) -> dict[str, Any] | None:
    row = (
        (
            await conn.execute(
                text("SELECT * FROM commercial.price_book WHERE price_book_id = :i"),
                {"i": uuid.UUID(price_book_id)},
            )
        )
        .mappings()
        .first()
    )
    return dict(row) if row else None


async def price_books(conn: AsyncConnection) -> list[dict[str, Any]]:
    rows = (
        await conn.execute(text("SELECT * FROM commercial.price_book ORDER BY version DESC"))
    ).mappings()
    return [dict(r) for r in rows]


async def lines(conn: AsyncConnection, price_book_id: str) -> list[dict[str, Any]]:
    rows = (
        await conn.execute(
            text(
                "SELECT l.sku_id, l.plan_code, l.unit_rate, l.unit_divisor, l.included_quantity, "
                "l.minimum_amount, s.unit, s.meter_id, s.consumption, s.released "
                "FROM commercial.price_book_line l JOIN commercial.sku s USING (sku_id) "
                "WHERE l.price_book_id = :i ORDER BY l.plan_code, l.sku_id"
            ),
            {"i": uuid.UUID(price_book_id)},
        )
    ).mappings()
    return [dict(r) for r in rows]


async def create_draft(
    conn: AsyncConnection, *, copy_of: str | None, created_by: str, note: str | None
) -> dict[str, Any]:
    """A new DRAFT version, optionally a copy of an existing book's lines."""
    version = int(
        (
            await conn.execute(
                text("SELECT coalesce(max(version), 0) + 1 FROM commercial.price_book")
            )
        ).scalar_one()
    )
    new_id = uuid.uuid4()
    currency = "USD"
    if copy_of:
        src = await price_book(conn, copy_of)
        if src is None:
            raise CommercialError("PRICE_BOOK_NOT_FOUND")
        currency = src["currency"]
    await conn.execute(
        text(
            "INSERT INTO commercial.price_book (price_book_id, version, currency, status, created_by, note) "
            "VALUES (:i, :v, :c, 'DRAFT', :a, :n)"
        ),
        {"i": new_id, "v": version, "c": currency, "a": uuid.UUID(created_by), "n": note},
    )
    if copy_of:
        await conn.execute(
            text(
                "INSERT INTO commercial.price_book_line (price_book_id, sku_id, plan_code, unit_rate, "
                "unit_divisor, included_quantity, minimum_amount) "
                "SELECT :new, sku_id, plan_code, unit_rate, unit_divisor, included_quantity, minimum_amount "
                "FROM commercial.price_book_line WHERE price_book_id = :old"
            ),
            {"new": new_id, "old": uuid.UUID(copy_of)},
        )
    return {
        "price_book_id": str(new_id),
        "version": version,
        "status": "DRAFT",
        "currency": currency,
    }


async def put_line(
    conn: AsyncConnection,
    price_book_id: str,
    *,
    sku_id: str,
    plan_code: str,
    unit_rate: Decimal,
    unit_divisor: int,
    included_quantity: int | None,
    minimum_amount: Decimal | None,
) -> None:
    await conn.execute(
        text(
            "INSERT INTO commercial.price_book_line (price_book_id, sku_id, plan_code, unit_rate, unit_divisor, "
            "included_quantity, minimum_amount) VALUES (:b, :s, :p, :r, :d, :q, :m) "
            "ON CONFLICT (price_book_id, sku_id, plan_code) DO UPDATE SET unit_rate = EXCLUDED.unit_rate, "
            "unit_divisor = EXCLUDED.unit_divisor, included_quantity = EXCLUDED.included_quantity, "
            "minimum_amount = EXCLUDED.minimum_amount"
        ),
        {
            "b": uuid.UUID(price_book_id),
            "s": sku_id,
            "p": plan_code,
            "r": unit_rate,
            "d": unit_divisor,
            "q": included_quantity,
            "m": minimum_amount,
        },
    )


async def approve(
    conn: AsyncConnection,
    price_book_id: str,
    *,
    approver: str,
    effective_from: datetime,
    now: datetime,
) -> None:
    """DRAFT -> APPROVED by someone other than the author (also checked by the table); the previously
    approved book becomes SUPERSEDED. Rates already referenced by an entitlement are not touched."""
    book = await price_book(conn, price_book_id)
    if book is None:
        raise CommercialError("PRICE_BOOK_NOT_FOUND")
    if book["status"] != "DRAFT":
        raise CommercialError("PRICE_BOOK_NOT_DRAFT")
    if book["created_by"] is not None and str(book["created_by"]) == approver:
        raise CommercialError(
            "PRICE_BOOK_SELF_APPROVAL", "the author cannot approve their own price book"
        )
    n = (
        await conn.execute(
            text("SELECT count(*) FROM commercial.price_book_line WHERE price_book_id = :i"),
            {"i": uuid.UUID(price_book_id)},
        )
    ).scalar_one()
    if not n:
        raise CommercialError("PRICE_BOOK_EMPTY", "a price book needs lines")
    await conn.execute(
        text(
            "UPDATE commercial.price_book SET status = 'SUPERSEDED' WHERE status = 'APPROVED' AND version < :v"
        ),
        {"v": book["version"]},
    )
    await conn.execute(
        text(
            "UPDATE commercial.price_book SET status = 'APPROVED', approved_by = :a, approved_at = :n, "
            "effective_from = :e WHERE price_book_id = :i"
        ),
        {"a": uuid.UUID(approver), "n": now, "e": effective_from, "i": uuid.UUID(price_book_id)},
    )


async def readiness(conn: AsyncConnection, price_book_id: str) -> dict[str, Any]:
    """What a price book still lacks before an approver should approve it.

    Every released, priced SKU (flat or metered) needs a line for every published plan that is not Enterprise (Enterprise is priced by contract). A line at
    a zero rate is reported as FREE so an approver sees it was chosen. Contract and pass-through SKUs are never priced from the book."""
    missing = (
        await conn.execute(
            text(
                "SELECT s.sku_id, s.description, p.plan_code FROM commercial.sku s CROSS JOIN commercial.plan p "
                "WHERE s.released AND s.retired_at IS NULL AND s.consumption IN ('FLAT', 'METERED') "
                "AND p.published AND p.plan_code <> 'ENTERPRISE' "
                "AND NOT EXISTS (SELECT 1 FROM commercial.price_book_line l WHERE l.price_book_id = :i AND l.sku_id = s.sku_id AND l.plan_code = p.plan_code) "
                "ORDER BY p.sort_order, s.sku_id"
            ),
            {"i": uuid.UUID(price_book_id)},
        )
    ).all()
    free = (
        await conn.execute(
            text("SELECT sku_id, plan_code FROM commercial.price_book_line WHERE price_book_id = :i AND unit_rate = 0 AND coalesce(minimum_amount, 0) = 0 ORDER BY plan_code, sku_id"),
            {"i": uuid.UUID(price_book_id)},
        )
    ).all()
    total = (await conn.execute(text("SELECT count(*) FROM commercial.price_book_line WHERE price_book_id = :i"), {"i": uuid.UUID(price_book_id)})).scalar_one()
    return {
        "price_book_id": price_book_id,
        "lines": int(total),
        "ready": not missing and total > 0,
        "missing": [{"sku": r.sku_id, "plan": r.plan_code, "what": r.description} for r in missing],
        "free": [{"sku": r.sku_id, "plan": r.plan_code} for r in free],
    }
