"""Partners, their child tenants and settlement entries (`commercial.partner*`, migration commercial_0032; ZID-COM-WF-2026-11 WF-I06).

The rules are in `domain/partner.py` and repeated by the database (one live channel per child, terms fixed after activation, a different person to activate and to decide a
settlement). Functions here read and write and name the refusal; they add no rule of their own.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.commercial.domain.catalogue import CommercialError
from zid_platform.modules.commercial.domain.partner import BILL_TO, KINDS, Child, Partner
from zid_platform.modules.commercial.infrastructure.plan_request_store import operator_scope

_COLS = (
    "partner_id, name, kind, parent_tenant_id, bill_to, territory, agreement_ref, brand_terms_ref, settlement_ref, status, "
    "effective_from, effective_until, created_by, created_at, activated_by, activated_at"
)


def model(r: dict[str, Any]) -> Partner:
    return Partner(
        partner_id=str(r["partner_id"]),
        parent_tenant_id=str(r["parent_tenant_id"]),
        bill_to=r["bill_to"],
        territory=tuple(r["territory"]),
        status=r["status"],
        effective_from=r["effective_from"],
        effective_until=r["effective_until"],
    )


async def get(conn: AsyncConnection, partner_id: str) -> dict[str, Any] | None:
    r = (
        (
            await conn.execute(
                text(f"SELECT {_COLS} FROM commercial.partner WHERE partner_id = :i"),
                {"i": uuid.UUID(partner_id)},
            )
        )
        .mappings()
        .first()
    )
    return dict(r) if r else None


async def partners(conn: AsyncConnection) -> list[dict[str, Any]]:
    rows = (
        (
            await conn.execute(
                text(f"SELECT {_COLS} FROM commercial.partner ORDER BY created_at DESC")
            )
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


async def create(
    conn: AsyncConnection,
    *,
    name: str,
    kind: str,
    parent_tenant_id: str,
    bill_to: str,
    territory: list[str],
    agreement_ref: str,
    brand_terms_ref: str | None,
    settlement_ref: str | None,
    effective_from: datetime | None,
    effective_until: datetime | None,
    created_by: str,
) -> dict[str, Any]:
    if kind not in KINDS:
        raise CommercialError("PARTNER_KIND_UNKNOWN", kind)
    if bill_to not in BILL_TO:
        raise CommercialError("PARTNER_BILL_TO_UNKNOWN", bill_to)
    pid = uuid.uuid4()
    try:
        async with conn.begin_nested():
            await conn.execute(
                text(
                    "INSERT INTO commercial.partner (partner_id, name, kind, parent_tenant_id, bill_to, territory, agreement_ref, "
                    "brand_terms_ref, settlement_ref, effective_from, effective_until, created_by) "
                    "VALUES (:i, :n, :k, :p, :b, :t, :a, :br, :s, :f, :u, :c)"
                ),
                {
                    "i": pid, "n": name.strip(), "k": kind, "p": uuid.UUID(parent_tenant_id), "b": bill_to,
                    "t": sorted(set(territory)), "a": agreement_ref.strip(), "br": brand_terms_ref, "s": settlement_ref,
                    "f": effective_from, "u": effective_until, "c": uuid.UUID(created_by),
                },
            )  # fmt: skip
    except DBAPIError as e:
        raise CommercialError("PARTNER_REFUSED", str(e.orig)[:160]) from e
    out = await get(conn, str(pid))
    assert out is not None
    return out


async def activate(
    conn: AsyncConnection,
    partner_id: str,
    *,
    activator: str,
    effective_from: datetime,
    now: datetime,
) -> None:
    p = await get(conn, partner_id)
    if p is None:
        raise CommercialError("PARTNER_NOT_FOUND")
    if p["status"] != "PROPOSED":
        raise CommercialError("PARTNER_NOT_PROPOSED")
    if str(p["created_by"]) == activator:
        raise CommercialError(
            "PARTNER_SELF_ACTIVATION", "the author cannot activate their own partner"
        )
    try:
        async with conn.begin_nested():
            await conn.execute(
                text(
                    "UPDATE commercial.partner SET status = 'ACTIVE', activated_by = :a, activated_at = :n, "
                    "effective_from = :f WHERE partner_id = :i"
                ),
                {
                    "a": uuid.UUID(activator),
                    "n": now,
                    "f": effective_from,
                    "i": uuid.UUID(partner_id),
                },
            )
    except DBAPIError as e:
        raise CommercialError("PARTNER_REFUSED", str(e.orig)[:160]) from e


async def set_status(conn: AsyncConnection, partner_id: str, status: str) -> None:
    """ACTIVE, SUSPENDED or TERMINATED. Termination is final and revokes new operations at once; it erases no evidence."""
    p = await get(conn, partner_id)
    if p is None:
        raise CommercialError("PARTNER_NOT_FOUND")
    try:
        async with conn.begin_nested():
            await conn.execute(
                text("UPDATE commercial.partner SET status = :s WHERE partner_id = :i"),
                {"s": status, "i": uuid.UUID(partner_id)},
            )
    except DBAPIError as e:
        raise CommercialError("PARTNER_STATUS_REFUSED", str(e.orig)[:160]) from e


async def children(conn: AsyncConnection, partner_id: str) -> list[dict[str, Any]]:
    rows = (
        (
            await conn.execute(
                text(
                    "SELECT child_tenant_id, permitted_by, permitted_at, downstream_consent_ref, usage_visible, revoked_at "
                    "FROM commercial.partner_child WHERE partner_id = :i ORDER BY permitted_at"
                ),
                {"i": uuid.UUID(partner_id)},
            )
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


async def load_child(conn: AsyncConnection, partner_id: str, child_tenant_id: str) -> Child | None:
    r = (
        (
            await conn.execute(
                text(
                    "SELECT downstream_consent_ref, revoked_at FROM commercial.partner_child "
                    "WHERE partner_id = :p AND child_tenant_id = :c"
                ),
                {"p": uuid.UUID(partner_id), "c": uuid.UUID(child_tenant_id)},
            )
        )
        .mappings()
        .first()
    )
    if r is None:
        return None
    return Child(child_tenant_id, r["revoked_at"] is not None, r["downstream_consent_ref"])


async def add_child(
    conn: AsyncConnection,
    partner_id: str,
    *,
    child_tenant_id: str,
    permitted_by: str,
    downstream_consent_ref: str | None,
    usage_visible: bool,
) -> None:
    p = await get(conn, partner_id)
    if p is None:
        raise CommercialError("PARTNER_NOT_FOUND")
    if p["status"] == "TERMINATED":
        raise CommercialError("PARTNER_TERMINATED")
    if str(p["parent_tenant_id"]) == child_tenant_id:
        raise CommercialError("CHILD_IS_PARTNER", "a partner is not its own child")
    try:
        async with conn.begin_nested():
            await conn.execute(
                text(
                    "INSERT INTO commercial.partner_child (partner_id, child_tenant_id, permitted_by, downstream_consent_ref, usage_visible) "
                    "VALUES (:p, :c, :b, :r, :v)"
                ),
                {
                    "p": uuid.UUID(partner_id), "c": uuid.UUID(child_tenant_id), "b": uuid.UUID(permitted_by),
                    "r": downstream_consent_ref, "v": usage_visible,
                },
            )  # fmt: skip
    except IntegrityError as e:
        raise CommercialError(
            "CHILD_ALREADY_IN_CHANNEL", "a child belongs to one live partner at a time"
        ) from e


async def revoke_child(conn: AsyncConnection, partner_id: str, child_tenant_id: str) -> None:
    done = await conn.execute(
        text(
            "UPDATE commercial.partner_child SET revoked_at = now() "
            "WHERE partner_id = :p AND child_tenant_id = :c AND revoked_at IS NULL"
        ),
        {"p": uuid.UUID(partner_id), "c": uuid.UUID(child_tenant_id)},
    )
    if not done.rowcount:
        raise CommercialError("CHILD_NOT_FOUND")


async def attributed_events(
    conn: AsyncConnection, partner_id: str, month: date
) -> list[dict[str, Any]]:
    """The events attributed to a partner in a month, across companies, for reconciliation. Operator plane only, read only."""
    async with operator_scope(conn):
        rows = (
            (
                await conn.execute(
                    text(
                        "SELECT tenant_id, deduplication_key, meter_id, quantity, legal_payer_id FROM commercial.billable_event "
                        "WHERE partner_id = :p AND window_month = :m ORDER BY occurred_at"
                    ),
                    {"p": uuid.UUID(partner_id), "m": month},
                )
            )
            .mappings()
            .all()
        )
    return [dict(r) for r in rows]


async def propose_settlement(
    conn: AsyncConnection,
    partner_id: str,
    *,
    period: date,
    kind: str,
    amount: str,
    currency: str,
    proposed_by: str,
) -> str:
    sid = uuid.uuid4()
    try:
        async with conn.begin_nested():
            await conn.execute(
                text(
                    "INSERT INTO commercial.partner_settlement (settlement_id, partner_id, period, kind, amount, currency, proposed_by) "
                    "VALUES (:i, :p, :d, :k, :a, :c, :b)"
                ),
                {
                    "i": sid, "p": uuid.UUID(partner_id), "d": period, "k": kind, "a": amount, "c": currency,
                    "b": uuid.UUID(proposed_by),
                },
            )  # fmt: skip
    except DBAPIError as e:
        raise CommercialError("SETTLEMENT_REFUSED", str(e.orig)[:160]) from e
    return str(sid)


async def decide_settlement(
    conn: AsyncConnection, settlement_id: str, *, decider: str, approve: bool, reason: str
) -> None:
    r = (
        (
            await conn.execute(
                text(
                    "SELECT status, proposed_by FROM commercial.partner_settlement WHERE settlement_id = :i"
                ),
                {"i": uuid.UUID(settlement_id)},
            )
        )
        .mappings()
        .first()
    )
    if r is None:
        raise CommercialError("SETTLEMENT_NOT_FOUND")
    if r["status"] != "PROPOSED":
        raise CommercialError("SETTLEMENT_ALREADY_DECIDED")
    if str(r["proposed_by"]) == decider:
        raise CommercialError(
            "SETTLEMENT_SELF_APPROVAL", "the proposer cannot decide their own entry"
        )
    await conn.execute(
        text(
            "UPDATE commercial.partner_settlement SET status = :s, decided_by = :d, decided_at = now(), reason = :r "
            "WHERE settlement_id = :i"
        ),
        {
            "s": "APPROVED" if approve else "REJECTED", "d": uuid.UUID(decider), "r": reason,
            "i": uuid.UUID(settlement_id),
        },
    )  # fmt: skip
