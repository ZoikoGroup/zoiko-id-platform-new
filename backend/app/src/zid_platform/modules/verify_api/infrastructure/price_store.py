"""The operator-set price list in PostgreSQL (CR-161). Anyone may read it; only the operator scope may write (`go_live_store.operator_scope`). Approved rows are never updated or deleted."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

_PRICE = "price_id, meter, currency, unit_rate, effective_from, status, proposed_by, decided_by, note, created_at, decided_at"


def _row(r: Any) -> dict[str, Any]:
    return dict(r._mapping)


async def insert_price(
    conn: AsyncConnection,
    *,
    price_id: str,
    meter: str,
    currency: str,
    unit_rate: Decimal,
    effective_from: date,
    proposed_by: str,
) -> None:
    await conn.execute(
        text(
            "INSERT INTO verify.api_price (price_id, meter, currency, unit_rate, effective_from, proposed_by) "
            "VALUES (:i, :m, :c, :r, :f, :by)"
        ),
        {
            "i": uuid.UUID(price_id),
            "m": meter,
            "c": currency,
            "r": unit_rate,
            "f": effective_from,
            "by": uuid.UUID(proposed_by),
        },
    )


async def get_price(
    conn: AsyncConnection, price_id: str, *, lock: bool = False
) -> dict[str, Any] | None:
    row = (
        await conn.execute(
            text(
                f"SELECT {_PRICE} FROM verify.api_price WHERE price_id = :i"
                + (" FOR UPDATE" if lock else "")
            ),
            {"i": uuid.UUID(price_id)},
        )
    ).first()
    return _row(row) if row else None


async def list_prices(conn: AsyncConnection, *, limit: int = 100) -> list[dict[str, Any]]:
    rows = (
        await conn.execute(
            text(
                f"SELECT {_PRICE} FROM verify.api_price ORDER BY effective_from DESC, created_at DESC LIMIT :n"
            ),
            {"n": limit},
        )
    ).all()
    return [_row(r) for r in rows]


async def approved_prices(conn: AsyncConnection) -> list[dict[str, Any]]:
    rows = (
        await conn.execute(
            text(
                f"SELECT {_PRICE} FROM verify.api_price WHERE status = 'APPROVED' ORDER BY effective_from"
            )
        )
    ).all()
    return [_row(r) for r in rows]


async def decide_price(
    conn: AsyncConnection,
    price_id: str,
    status: str,
    decided_by: str,
    note: str | None,
    now: datetime,
) -> None:
    await conn.execute(
        text(
            "UPDATE verify.api_price SET status = :s, decided_by = :d, note = :n, decided_at = :at "
            "WHERE price_id = :i AND status = 'PROPOSED'"
        ),
        {
            "i": uuid.UUID(price_id),
            "s": status,
            "d": uuid.UUID(decided_by),
            "n": note or None,
            "at": now,
        },
    )
