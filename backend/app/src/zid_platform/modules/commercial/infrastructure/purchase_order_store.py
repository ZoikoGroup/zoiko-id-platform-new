"""Purchase orders and their draw-down (`commercial.purchase_order*`, migration commercial_0033; ZID-COM-WF-2026-11 CW-11). Runs on the caller's tenant scope."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.commercial.domain import purchase_order as rules
from zid_platform.modules.commercial.domain.catalogue import CommercialError
from zid_platform.modules.commercial.infrastructure.plan_request_store import operator_scope

_SELECT = (
    "SELECT p.po_id, p.tenant_id, p.po_number, p.currency, p.value, p.valid_from, p.valid_until, p.status, p.created_at, "
    "coalesce((SELECT sum(amount) FROM commercial.purchase_order_draw d WHERE d.po_id = p.po_id), 0) AS drawn "
    "FROM commercial.purchase_order p"
)


def _model(r: Any) -> rules.PO:
    return rules.PO(
        str(r["po_id"]),
        r["po_number"],
        r["currency"],
        Decimal(r["value"]),
        Decimal(r["drawn"]),
        r["valid_from"],
        r["valid_until"],
        r["status"],
    )


async def for_tenant(conn: AsyncConnection, tenant_id: str) -> list[dict[str, Any]]:
    rows = (
        (
            await conn.execute(
                text(_SELECT + " WHERE p.tenant_id = :t ORDER BY p.valid_until, p.po_number"),
                {"t": uuid.UUID(tenant_id)},
            )
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


async def record(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    po_number: str,
    currency: str,
    value: str,
    valid_from: date,
    valid_until: date,
    created_by: str,
) -> str:
    po_id = uuid.uuid4()
    try:
        async with conn.begin_nested():
            await conn.execute(
                text(
                    "INSERT INTO commercial.purchase_order (po_id, tenant_id, po_number, currency, value, valid_from, valid_until, created_by) "
                    "VALUES (:i, :t, :n, :c, :v, :f, :u, :b)"
                ),
                {
                    "i": po_id, "t": uuid.UUID(tenant_id), "n": po_number.strip(), "c": currency, "v": value,
                    "f": valid_from, "u": valid_until, "b": uuid.UUID(created_by),
                },
            )  # fmt: skip
    except IntegrityError as e:
        raise CommercialError("PO_NUMBER_EXISTS", po_number) from e
    except DBAPIError as e:
        raise CommercialError("PO_REFUSED", str(e.orig)[:160]) from e
    return str(po_id)


async def close(conn: AsyncConnection, po_id: str) -> None:
    done = await conn.execute(
        text(
            "UPDATE commercial.purchase_order SET status = 'CLOSED', closed_at = now() WHERE po_id = :i AND status = 'ACTIVE'"
        ),
        {"i": uuid.UUID(po_id)},
    )
    if not done.rowcount:
        raise CommercialError("PO_NOT_OPEN")


async def operator_list(conn: AsyncConnection, tenant_id: str) -> list[dict[str, Any]]:
    async with operator_scope(conn):
        return await for_tenant(conn, tenant_id)


async def draw_for_invoice(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    invoice_id: str,
    currency: str,
    amount: Decimal,
    on: date,
) -> str | None:
    """Draw an invoice against the PO that fits, and return its id; None when the company has no POs on record. Raises when it has some and none fits."""
    rows = await for_tenant(conn, tenant_id)
    try:
        chosen = rules.choose([_model(r) for r in rows], currency=currency, amount=amount, on=on)
    except rules.PurchaseOrderError as e:
        raise CommercialError(e.code, e.detail) from e
    if chosen is None:
        return None
    await conn.execute(
        text(
            "INSERT INTO commercial.purchase_order_draw (invoice_id, po_id, tenant_id, amount) VALUES (:i, :p, :t, :a)"
        ),
        {
            "i": uuid.UUID(invoice_id),
            "p": uuid.UUID(chosen.po_id),
            "t": uuid.UUID(tenant_id),
            "a": amount,
        },
    )
    return chosen.number
