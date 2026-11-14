"""Public sales inquiries (`commercial.sales_lead`, migration commercial_0038; ZID-UX-PRICE-004 WF-P07). Platform data, no tenant. The rules are repeated by the database."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.commercial.domain import sales_lead as rules
from zid_platform.modules.commercial.domain.catalogue import CommercialError

MAX_PER_EMAIL_PER_DAY = 5


async def _open_for(conn: AsyncConnection, key: str) -> dict[str, Any] | None:
    r = (
        (
            await conn.execute(
                text(
                    "SELECT lead_id, status FROM commercial.sales_lead WHERE dedupe_key = :k AND status NOT IN ('CLOSED', 'SPAM')"
                ),
                {"k": key},
            )
        )
        .mappings()
        .first()
    )
    return dict(r) if r else None


async def submit(
    conn: AsyncConnection, lead: rules.Lead, *, now: datetime | None = None
) -> dict[str, Any]:
    """Record one inquiry, or return the open one it repeats. It writes one row in this table and nothing else: no tenant, entitlement, contract or payment object."""
    at = now or datetime.now(UTC)
    key = rules.dedupe_key(lead)
    existing = await _open_for(conn, key)
    if existing:
        return {
            "lead_id": str(existing["lead_id"]),
            "status": existing["status"],
            "duplicate": True,
        }
    recent = (
        await conn.execute(
            text(
                "SELECT count(*) FROM commercial.sales_lead WHERE work_email = :e AND created_at > :s"
            ),
            {"e": lead.work_email, "s": at - timedelta(days=1)},
        )
    ).scalar_one()
    if int(recent) >= MAX_PER_EMAIL_PER_DAY:
        raise CommercialError("LEAD_RATE_LIMITED", "too many inquiries from this address today")
    lead_id = uuid.uuid4()
    try:
        async with conn.begin_nested():
            await conn.execute(
                text(
                    "INSERT INTO commercial.sales_lead (lead_id, kind, partner_type, contact_name, work_email, company, country, region, capabilities, "
                    "volume_band, entities, referral_source, security_needs, notice_version, dedupe_key, created_at, updated_at) "
                    "VALUES (:i, :k, :pt, :n, :e, :c, :co, :r, :cap, :v, :en, :rs, :sn, :nv, :dk, :t, :t)"
                ),
                {
                    "i": lead_id, "k": lead.kind, "pt": lead.partner_type, "n": lead.contact_name, "e": lead.work_email, "c": lead.company,
                    "co": lead.country, "r": lead.region, "cap": list(lead.capabilities), "v": lead.volume_band, "en": lead.entities,
                    "rs": lead.referral_source, "sn": lead.security_needs, "nv": lead.notice_version, "dk": key, "t": at,
                },
            )  # fmt: skip
    except IntegrityError:  # two submissions raced: the other one won, and this one is its repeat
        again = await _open_for(conn, key)
        if again:
            return {"lead_id": str(again["lead_id"]), "status": again["status"], "duplicate": True}
        raise
    except DBAPIError as e:
        raise CommercialError("LEAD_REFUSED", str(e.orig)[:160]) from e
    return {"lead_id": str(lead_id), "status": "RECEIVED", "duplicate": False}


_COLS = (
    "lead_id, kind, partner_type, contact_name, work_email, company, country, region, capabilities, volume_band, entities, referral_source, "
    "security_needs, notice_version, status, status_reason, decided_by, created_at, updated_at"
)


async def leads(
    conn: AsyncConnection, status: str | None = None, limit: int = 200
) -> list[dict[str, Any]]:
    rows = (
        (
            await conn.execute(
                text(
                    f"SELECT {_COLS} FROM commercial.sales_lead WHERE (CAST(:s AS text) IS NULL OR status = CAST(:s AS text)) "
                    "ORDER BY created_at DESC LIMIT :n"
                ),
                {"s": status, "n": limit},
            )
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


async def advance(
    conn: AsyncConnection, lead_id: str, *, to: str, reason: str | None, decided_by: str
) -> None:
    r = (
        (
            await conn.execute(
                text("SELECT status FROM commercial.sales_lead WHERE lead_id = :i"),
                {"i": uuid.UUID(lead_id)},
            )
        )
        .mappings()
        .first()
    )
    if r is None:
        raise CommercialError("LEAD_NOT_FOUND")
    if to not in rules.next_states(r["status"]):
        raise CommercialError("LEAD_TRANSITION_REFUSED", f"{r['status']} to {to}")
    if to in rules.TERMINAL and not (reason or "").strip():
        raise CommercialError("LEAD_REASON_REQUIRED", "closing or marking spam needs a reason")
    try:
        async with conn.begin_nested():
            await conn.execute(
                text(
                    "UPDATE commercial.sales_lead SET status = :s, status_reason = :r, decided_by = :d, updated_at = now() WHERE lead_id = :i"
                ),
                {
                    "s": to,
                    "r": (reason or "").strip() or None,
                    "d": uuid.UUID(decided_by),
                    "i": uuid.UUID(lead_id),
                },
            )
    except DBAPIError as e:
        raise CommercialError("LEAD_TRANSITION_REFUSED", str(e.orig)[:160]) from e
