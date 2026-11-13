"""FX rate sets (`commercial.fx_rate*`, migration commercial_0035; ZID-COM-WF-2026-11 section 05.2). The rules are repeated by the database."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.commercial.domain.catalogue import CommercialError


async def sets(conn: AsyncConnection) -> list[dict[str, Any]]:
    rows = (
        (await conn.execute(text("SELECT * FROM commercial.fx_rate_set ORDER BY version DESC")))
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


async def get(conn: AsyncConnection, set_id: str) -> dict[str, Any] | None:
    r = (
        (
            await conn.execute(
                text("SELECT * FROM commercial.fx_rate_set WHERE set_id = :i"),
                {"i": uuid.UUID(set_id)},
            )
        )
        .mappings()
        .first()
    )
    return dict(r) if r else None


async def rates(conn: AsyncConnection, set_id: str) -> dict[str, Decimal]:
    rows = (
        await conn.execute(
            text(
                "SELECT currency, usd_per_unit FROM commercial.fx_rate WHERE set_id = :i ORDER BY currency"
            ),
            {"i": uuid.UUID(set_id)},
        )
    ).all()
    return {r[0]: Decimal(r[1]) for r in rows}


async def active(conn: AsyncConnection) -> tuple[dict[str, Any] | None, dict[str, Decimal]]:
    """The ACTIVE rate set and its rates. None and no rates when there is none: nothing is converted."""
    r = (
        (
            await conn.execute(
                text(
                    "SELECT * FROM commercial.fx_rate_set WHERE status = 'ACTIVE' ORDER BY version DESC LIMIT 1"
                )
            )
        )
        .mappings()
        .first()
    )
    if r is None:
        return None, {}
    return dict(r), await rates(conn, str(r["set_id"]))


async def create_draft(
    conn: AsyncConnection, *, source_ref: str, as_of: date, created_by: str
) -> dict[str, Any]:
    version = int(
        (
            await conn.execute(
                text("SELECT coalesce(max(version), 0) + 1 FROM commercial.fx_rate_set")
            )
        ).scalar_one()
    )
    set_id = uuid.uuid4()
    try:
        async with conn.begin_nested():
            await conn.execute(
                text(
                    "INSERT INTO commercial.fx_rate_set (set_id, version, status, source_ref, as_of, created_by) "
                    "VALUES (:i, :v, 'DRAFT', :s, :a, :c)"
                ),
                {
                    "i": set_id,
                    "v": version,
                    "s": source_ref.strip(),
                    "a": as_of,
                    "c": uuid.UUID(created_by),
                },
            )
    except DBAPIError as e:
        raise CommercialError("FX_SET_REFUSED", str(e.orig)[:160]) from e
    return {"set_id": str(set_id), "version": version, "status": "DRAFT"}


async def put_rate(conn: AsyncConnection, set_id: str, currency: str, usd_per_unit: str) -> None:
    s = await get(conn, set_id)
    if s is None:
        raise CommercialError("FX_SET_NOT_FOUND")
    if s["status"] != "DRAFT":
        raise CommercialError("FX_SET_NOT_DRAFT", "an activated rate set is immutable")
    try:
        async with conn.begin_nested():
            await conn.execute(
                text(
                    "INSERT INTO commercial.fx_rate (set_id, currency, usd_per_unit) VALUES (:i, :c, :r) "
                    "ON CONFLICT (set_id, currency) DO UPDATE SET usd_per_unit = EXCLUDED.usd_per_unit"
                ),
                {"i": uuid.UUID(set_id), "c": currency, "r": usd_per_unit},
            )
    except DBAPIError as e:
        raise CommercialError("FX_RATE_REFUSED", str(e.orig)[:160]) from e


async def activate(conn: AsyncConnection, set_id: str, *, activator: str, now: datetime) -> None:
    """DRAFT to ACTIVE by someone other than the author; the previous set is superseded."""
    s = await get(conn, set_id)
    if s is None:
        raise CommercialError("FX_SET_NOT_FOUND")
    if s["status"] != "DRAFT":
        raise CommercialError("FX_SET_NOT_DRAFT")
    if str(s["created_by"]) == activator:
        raise CommercialError("FX_SELF_ACTIVATION", "the author cannot activate their own rate set")
    try:
        async with conn.begin_nested():
            await conn.execute(
                text(
                    "UPDATE commercial.fx_rate_set SET status = 'SUPERSEDED' WHERE status = 'ACTIVE' AND version < :v"
                ),
                {"v": s["version"]},
            )
            await conn.execute(
                text(
                    "UPDATE commercial.fx_rate_set SET status = 'ACTIVE', activated_by = :a, activated_at = :n WHERE set_id = :i"
                ),
                {"a": uuid.UUID(activator), "n": now, "i": uuid.UUID(set_id)},
            )
    except DBAPIError as e:
        raise CommercialError("FX_ACTIVATION_REFUSED", str(e.orig)[:160]) from e


async def withdraw(conn: AsyncConnection, set_id: str) -> None:
    s = await get(conn, set_id)
    if s is None:
        raise CommercialError("FX_SET_NOT_FOUND")
    if s["status"] != "DRAFT":
        raise CommercialError("FX_SET_NOT_DRAFT")
    await conn.execute(
        text("UPDATE commercial.fx_rate_set SET status = 'WITHDRAWN' WHERE set_id = :i"),
        {"i": uuid.UUID(set_id)},
    )
