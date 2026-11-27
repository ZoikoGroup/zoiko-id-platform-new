"""The operator-set price of a Verify API check (CR-161; ZID-EXT-IDV-001 section 12, WF-15, decision D12).

An operator proposes a price for a usage fact from the first day of a later month; a DIFFERENT operator approves or rejects it. Approved prices are shown to companies as an INDICATIVE cost on their usage screen, with the
price used named on every line. Nothing here invoices anyone or touches the commercial price book: invoicing a verification is a separate step the commercial estate owns.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.commercial.ports import MINOR_UNITS
from zid_platform.modules.verify_api.application import approval_policy, observe
from zid_platform.modules.verify_api.domain import model as m
from zid_platform.modules.verify_api.domain import pricing as p
from zid_platform.modules.verify_api.infrastructure import price_store as db
from zid_platform.modules.verify_api.infrastructure import single_approver


def price_view(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "price_id": str(r["price_id"]),
        "meter": r["meter"],
        "label": p.METERS.get(r["meter"], r["meter"]),
        "currency": r["currency"],
        "unit_rate": format(r["unit_rate"].normalize(), "f"),
        "effective_from": r["effective_from"].isoformat(),
        "status": r["status"],
        "proposed_by": str(r["proposed_by"]),
        "decided_by": str(r["decided_by"]) if r["decided_by"] else None,
        "note": r["note"],
        "created_at": r["created_at"].isoformat(),
        "decided_at": r["decided_at"].isoformat() if r["decided_at"] else None,
    }


async def list_view(conn: AsyncConnection) -> dict[str, Any]:
    return {
        "items": [price_view(r) for r in await db.list_prices(conn)],
        "meters": [{"id": k, "label": v} for k, v in p.METERS.items()],
        "currencies": sorted(MINOR_UNITS),
    }


async def approved(conn: AsyncConnection) -> list[p.Price]:
    return [
        p.Price(r["meter"], r["currency"], r["unit_rate"], r["effective_from"], str(r["price_id"]))
        for r in await db.approved_prices(conn)
    ]


async def propose(
    conn: AsyncConnection, operator: str, body: dict[str, Any], now: datetime | None = None
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    clean = p.check_price(body, today=now.date(), minor_units=MINOR_UNITS)
    price_id = str(uuid.uuid4())
    await db.insert_price(conn, price_id=price_id, proposed_by=operator, **clean)
    made = await db.get_price(conn, price_id)
    assert made is not None
    return price_view(made)


async def _load(conn: AsyncConnection, price_id: str) -> dict[str, Any]:
    r = await db.get_price(conn, price_id, lock=True)
    if r is None:
        raise m.PartnerError("NOT_FOUND", "No such price.")
    if r["status"] != p.PriceStatus.PROPOSED:
        raise m.PartnerError(
            "PRICE_STATE_INVALID", f"A {r['status'].lower()} price cannot be decided again."
        )
    return r


async def approve(
    conn: AsyncConnection,
    price_id: str,
    operator: str,
    note: str | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """A different operator from the one who proposed it. It must still start in a month that has not begun: a price that waited too long is rejected, never applied late."""
    now = now or datetime.now(UTC)
    r = await _load(conn, price_id)
    if str(r["proposed_by"]) == operator and not approval_policy.single_approver():
        raise m.PartnerError(
            "PRICE_SELF_APPROVAL", "The person who proposed a price cannot also approve it."
        )
    await single_approver.apply(
        conn, approval_policy.single_approver()
    )  # the table's own different-person rule yields only in single-approver mode (verify_api_0019)
    if r["effective_from"] < p.first_of_next_month(now.date()):
        raise m.PartnerError(
            "PRICE_NOT_IN_FUTURE",
            "This price now starts in a month that has begun. Reject it and propose it again from a later month.",
        )
    clean = p.check_note(note, required=False)
    try:
        # a savepoint: the unique-index refusal below must not poison the whole transaction, or the operator scope could not even close cleanly
        async with conn.begin_nested():
            await db.decide_price(conn, price_id, p.PriceStatus.APPROVED, operator, clean, now)
    except Exception as e:  # the unique index: one approved price for a meter, currency and month
        if "api_price_one_approved" in str(e):
            raise m.PartnerError(
                "PRICE_EXISTS",
                "A price for that usage fact, currency and month is already approved. Propose the change from a later month.",
            ) from e
        raise
    observe.PRICES.inc(event="approved")
    fresh = await db.get_price(conn, price_id)
    assert fresh is not None
    return price_view(fresh)


async def reject(
    conn: AsyncConnection,
    price_id: str,
    operator: str,
    note: str | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    r = await _load(conn, price_id)
    if str(r["proposed_by"]) == operator:
        raise m.PartnerError(
            "PRICE_SELF_APPROVAL",
            "A price is decided by someone other than the person who proposed it.",
        )
    clean = p.check_note(note, required=True)
    await db.decide_price(conn, price_id, p.PriceStatus.REJECTED, operator, clean, now)
    observe.PRICES.inc(event="rejected")
    fresh = await db.get_price(conn, price_id)
    assert fresh is not None
    return price_view(fresh)
