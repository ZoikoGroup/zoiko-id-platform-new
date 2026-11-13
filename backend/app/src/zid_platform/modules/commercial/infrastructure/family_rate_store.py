"""The family rate card (`commercial.family_rate*`, migration commercial_0041; ZID-UX-PRICE-004 sections 03.3, 06). The rules are in `domain/family_rates.py` and repeated by the database: lines only on a
DRAFT, three different people (CFO, CCO, FOUNDER) none the author before activation, an activated card immutable."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.commercial.domain.catalogue import CommercialError
from zid_platform.modules.commercial.domain.family_rates import Rate

ROLES = ("CFO", "CCO", "FOUNDER")
_COLS = "family, country, period, currency, charge_unit, included_units, unit_amount, minimum_amount, minimum_model, source_ref"


def _rate(r: Any) -> Rate:
    return Rate(
        family=r["family"], country=r["country"], period=r["period"], currency=r["currency"], charge_unit=r["charge_unit"],
        included_units=int(r["included_units"]), unit_amount=Decimal(r["unit_amount"]),
        minimum_amount=Decimal(r["minimum_amount"]) if r["minimum_amount"] is not None else None, minimum_model=r["minimum_model"],
    )  # fmt: skip


async def card(conn: AsyncConnection, card_id: str) -> dict[str, Any] | None:
    r = (
        (
            await conn.execute(
                text("SELECT * FROM commercial.family_rate_card WHERE card_id = :i"),
                {"i": uuid.UUID(card_id)},
            )
        )
        .mappings()
        .first()
    )
    return dict(r) if r else None


async def cards(conn: AsyncConnection) -> list[dict[str, Any]]:
    return [
        dict(r)
        for r in (
            await conn.execute(
                text("SELECT * FROM commercial.family_rate_card ORDER BY version DESC")
            )
        )
        .mappings()
        .all()
    ]


async def lines(conn: AsyncConnection, card_id: str) -> list[tuple[Rate, str]]:
    """The rate lines of a card with the source reference of each."""
    rows = (
        await conn.execute(
            text(f"SELECT {_COLS} FROM commercial.family_rate WHERE card_id = :i ORDER BY family, country, period"),
            {"i": uuid.UUID(card_id)},
        )
    ).mappings().all()  # fmt: skip
    return [(_rate(r), r["source_ref"]) for r in rows]


async def approvals(conn: AsyncConnection, card_id: str) -> list[dict[str, Any]]:
    rows = (
        await conn.execute(
            text("SELECT role, approver, approved_at FROM commercial.family_rate_card_approval WHERE card_id = :i ORDER BY role"),
            {"i": uuid.UUID(card_id)},
        )
    ).mappings().all()  # fmt: skip
    return [dict(r) for r in rows]


async def active(conn: AsyncConnection, now: datetime) -> dict[str, Any] | None:
    r = (
        (
            await conn.execute(
                text(
                    "SELECT * FROM commercial.family_rate_card WHERE status = 'ACTIVE' AND effective_from <= :n ORDER BY version DESC LIMIT 1"
                ),
                {"n": now},
            )
        )
        .mappings()
        .first()
    )
    return dict(r) if r else None


async def active_rates(
    conn: AsyncConnection, now: datetime
) -> tuple[dict[str, Any] | None, dict[tuple[str, str, str], Rate]]:
    """The ACTIVE card in force at `now` and its lines by (family, country, period). None and nothing when no card is active: every service then reads Quote required."""
    c = await active(conn, now)
    if c is None:
        return None, {}
    return c, {(r.family, r.country, r.period): r for r, _ in await lines(conn, str(c["card_id"]))}


async def create_draft(
    conn: AsyncConnection, *, copy_of: str | None, created_by: str, note: str | None
) -> dict[str, Any]:
    version = int(
        (
            await conn.execute(
                text("SELECT coalesce(max(version), 0) + 1 FROM commercial.family_rate_card")
            )
        ).scalar_one()
    )
    new_id = uuid.uuid4()
    if copy_of and await card(conn, copy_of) is None:
        raise CommercialError("RATE_CARD_NOT_FOUND")
    await conn.execute(
        text(
            "INSERT INTO commercial.family_rate_card (card_id, version, status, created_by, note) VALUES (:i, :v, 'DRAFT', :a, :n)"
        ),
        {"i": new_id, "v": version, "a": uuid.UUID(created_by), "n": note},
    )
    if copy_of:  # lines are copied; approvals are NOT: a copy is signed afresh
        await conn.execute(
            text(
                f"INSERT INTO commercial.family_rate (card_id, {_COLS}) SELECT :new, {_COLS} FROM commercial.family_rate WHERE card_id = :old"
            ),
            {"new": new_id, "old": uuid.UUID(copy_of)},
        )
    return {"card_id": str(new_id), "version": version, "status": "DRAFT"}


async def put_line(conn: AsyncConnection, card_id: str, rate: Rate, source_ref: str) -> None:
    c = await card(conn, card_id)
    if c is None:
        raise CommercialError("RATE_CARD_NOT_FOUND")
    if c["status"] != "DRAFT":
        raise CommercialError("RATE_CARD_NOT_DRAFT", "an activated rate card is immutable")
    try:
        async with conn.begin_nested():
            await conn.execute(
                text(
                    f"INSERT INTO commercial.family_rate (card_id, {_COLS}) VALUES (:m, :family, :country, :period, :currency, :charge_unit, :included_units, "
                    ":unit_amount, :minimum_amount, :minimum_model, :source_ref) "
                    "ON CONFLICT (card_id, family, country, period) DO UPDATE SET currency = EXCLUDED.currency, charge_unit = EXCLUDED.charge_unit, "
                    "included_units = EXCLUDED.included_units, unit_amount = EXCLUDED.unit_amount, minimum_amount = EXCLUDED.minimum_amount, "
                    "minimum_model = EXCLUDED.minimum_model, source_ref = EXCLUDED.source_ref"
                ),
                {
                    "m": uuid.UUID(card_id), "family": rate.family, "country": rate.country, "period": rate.period, "currency": rate.currency,
                    "charge_unit": rate.charge_unit.strip(), "included_units": rate.included_units, "unit_amount": rate.unit_amount,
                    "minimum_amount": rate.minimum_amount, "minimum_model": rate.minimum_model, "source_ref": source_ref.strip(),
                },
            )  # fmt: skip
    except DBAPIError as e:
        raise CommercialError("RATE_LINE_REFUSED", str(e.orig)[:160]) from e


async def approve(conn: AsyncConnection, card_id: str, *, role: str, approver: str) -> None:
    if role not in ROLES:
        raise CommercialError("RATE_CARD_ROLE_UNKNOWN", role)
    c = await card(conn, card_id)
    if c is None:
        raise CommercialError("RATE_CARD_NOT_FOUND")
    if c["status"] != "DRAFT":
        raise CommercialError("RATE_CARD_NOT_DRAFT")
    if str(c["created_by"]) == approver:
        raise CommercialError(
            "RATE_CARD_SELF_APPROVAL", "the author cannot approve their own rate card"
        )
    existing = await approvals(conn, card_id)
    if any(a["role"] == role for a in existing):
        raise CommercialError("RATE_CARD_ROLE_ALREADY_SIGNED", role)
    if any(str(a["approver"]) == approver for a in existing):
        raise CommercialError(
            "RATE_CARD_PERSON_ALREADY_SIGNED", "one person cannot sign for two roles"
        )
    try:
        async with conn.begin_nested():
            await conn.execute(
                text(
                    "INSERT INTO commercial.family_rate_card_approval (card_id, role, approver) VALUES (:m, :r, :a)"
                ),
                {"m": uuid.UUID(card_id), "r": role, "a": uuid.UUID(approver)},
            )
    except IntegrityError as e:
        raise CommercialError("RATE_CARD_APPROVAL_REFUSED", str(e.orig)[:160]) from e
    except DBAPIError as e:
        raise CommercialError("RATE_CARD_APPROVAL_REFUSED", str(e.orig)[:160]) from e


async def withdraw(conn: AsyncConnection, card_id: str) -> None:
    c = await card(conn, card_id)
    if c is None:
        raise CommercialError("RATE_CARD_NOT_FOUND")
    if c["status"] != "DRAFT":
        raise CommercialError("RATE_CARD_NOT_DRAFT")
    await conn.execute(
        text("UPDATE commercial.family_rate_card SET status = 'WITHDRAWN' WHERE card_id = :i"),
        {"i": uuid.UUID(card_id)},
    )


async def activate(
    conn: AsyncConnection, card_id: str, *, activator: str, effective_from: datetime, now: datetime
) -> None:
    c = await card(conn, card_id)
    if c is None:
        raise CommercialError("RATE_CARD_NOT_FOUND")
    if c["status"] != "DRAFT":
        raise CommercialError("RATE_CARD_NOT_DRAFT")
    if str(c["created_by"]) == activator:
        raise CommercialError(
            "RATE_CARD_SELF_APPROVAL", "the author cannot activate their own rate card"
        )
    signed = {a["role"] for a in await approvals(conn, card_id)}
    missing = [r for r in ROLES if r not in signed]
    if missing:
        raise CommercialError("RATE_CARD_APPROVALS_MISSING", ",".join(missing))
    if not await lines(conn, card_id):
        raise CommercialError("RATE_CARD_EMPTY", "a rate card needs lines")
    await conn.execute(
        text(
            "UPDATE commercial.family_rate_card SET status = 'SUPERSEDED' WHERE status = 'ACTIVE' AND version < :v"
        ),
        {"v": c["version"]},
    )
    await conn.execute(
        text(
            "UPDATE commercial.family_rate_card SET status = 'ACTIVE', effective_from = :e, activated_by = :a, activated_at = :n WHERE card_id = :i"
        ),
        {"e": effective_from, "a": uuid.UUID(activator), "n": now, "i": uuid.UUID(card_id)},
    )
