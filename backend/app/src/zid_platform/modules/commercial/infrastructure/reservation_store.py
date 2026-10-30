"""Usage reservations (`commercial.usage_reservation`; ZID-ENG-DEC-008 section 05). One row per (tenant, idempotency key)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection


def _view(r: Any) -> dict[str, Any]:
    return {
        "reservation_id": str(r.reservation_id),
        "tenant_id": str(r.tenant_id),
        "meter": r.meter,
        "quantity": int(r.quantity),
        "period": r.period,
        "state": r.state,
        "outcome": r.outcome,
        "catalog_version": r.catalog_version,
        "created_at": r.created_at.isoformat(),
        "resolved_at": r.resolved_at.isoformat() if r.resolved_at else None,
        "idempotency_key": r.idempotency_key,
    }


async def lock(conn: AsyncConnection, tenant_id: str, meter: str, period: str) -> None:
    """Serialize reservations of one meter in one period for one tenant, so two concurrent requests cannot both fit under a cap."""
    await conn.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
        {"k": f"usage:{tenant_id}:{meter}:{period}"},
    )


async def by_key(conn: AsyncConnection, key: str) -> dict[str, Any] | None:
    row = (
        await conn.execute(
            text("SELECT * FROM commercial.usage_reservation WHERE idempotency_key = :k"),
            {"k": key},
        )
    ).first()
    return _view(row) if row else None


async def by_id(conn: AsyncConnection, reservation_id: str) -> dict[str, Any] | None:
    row = (
        await conn.execute(
            text("SELECT * FROM commercial.usage_reservation WHERE reservation_id = :i"),
            {"i": uuid.UUID(reservation_id)},
        )
    ).first()
    return _view(row) if row else None


async def totals(conn: AsyncConnection, meter: str, period: str) -> tuple[int, int]:
    """(committed, reserved) quantity for the meter in the period."""
    row = (
        await conn.execute(
            text(
                "SELECT coalesce(sum(quantity) FILTER (WHERE state = 'COMMITTED'), 0), "
                "coalesce(sum(quantity) FILTER (WHERE state = 'RESERVED'), 0) "
                "FROM commercial.usage_reservation WHERE meter = :m AND period = :p"
            ),
            {"m": meter, "p": period},
        )
    ).one()
    return int(row[0]), int(row[1])


async def insert(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    meter: str,
    quantity: int,
    key: str,
    period: str,
    catalog_version: str,
    now: datetime,
) -> dict[str, Any]:
    row = (
        await conn.execute(
            text("""
                INSERT INTO commercial.usage_reservation
                    (reservation_id, tenant_id, meter, quantity, idempotency_key, period, catalog_version, created_at)
                VALUES (:id, :t, :m, :q, :k, :p, :v, :now)
                RETURNING *
            """),
            {
                "id": uuid.uuid4(),
                "t": uuid.UUID(tenant_id),
                "m": meter,
                "q": quantity,
                "k": key,
                "p": period,
                "v": catalog_version,
                "now": now,
            },
        )
    ).one()
    return _view(row)


async def resolve(
    conn: AsyncConnection, reservation_id: str, state: str, outcome: str | None, now: datetime
) -> dict[str, Any] | None:
    """RESERVED to COMMITTED or RELEASED, once. None when the reservation is not RESERVED any more."""
    row = (
        await conn.execute(
            text("""
                UPDATE commercial.usage_reservation
                   SET state = :s, outcome = :o, resolved_at = :now
                 WHERE reservation_id = :i AND state = 'RESERVED'
                RETURNING *
            """),
            {"i": uuid.UUID(reservation_id), "s": state, "o": outcome, "now": now},
        )
    ).first()
    return _view(row) if row else None


async def per_meter(conn: AsyncConnection, period: str) -> dict[str, dict[str, int]]:
    rows = (
        await conn.execute(
            text(
                "SELECT meter, "
                "coalesce(sum(quantity) FILTER (WHERE state = 'COMMITTED'), 0) AS committed, "
                "coalesce(sum(quantity) FILTER (WHERE state = 'RESERVED'), 0) AS reserved, "
                "coalesce(sum(quantity) FILTER (WHERE state = 'RELEASED'), 0) AS released "
                "FROM commercial.usage_reservation WHERE period = :p GROUP BY meter"
            ),
            {"p": period},
        )
    ).all()
    return {
        r.meter: {
            "committed": int(r.committed),
            "reserved": int(r.reserved),
            "released": int(r.released),
        }
        for r in rows
    }
