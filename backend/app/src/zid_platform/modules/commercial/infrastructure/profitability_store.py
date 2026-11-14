"""Reads for profitability (ZID-COM-WF-2026-11 CW-13): rated revenue and provider cost by month. Read only; operator plane."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.commercial.domain.profitability import Cost
from zid_platform.modules.commercial.infrastructure.plan_request_store import operator_scope


async def revenue_by_tenant(
    conn: AsyncConnection, start: date, end: date
) -> list[tuple[date, str, str, str | None, Decimal]]:
    """(month, tenant, currency, sku, net) of the computed rating runs in [start, end], credits netted in. Months are first-of-month dates."""
    async with operator_scope(conn):
        rows = (
            await conn.execute(
                text(
                    "SELECT u.period, u.tenant_id::text, u.currency, u.sku, sum(u.net) FROM commercial.rated_usage u "
                    "JOIN commercial.rating_run r ON r.run_id = u.run_id AND r.state = 'COMPUTED' "
                    "WHERE u.period >= :s AND u.period <= :e GROUP BY 1, 2, 3, 4"
                ),
                {"s": start, "e": end},
            )
        ).all()
    return [(r[0], r[1], r[2], r[3], Decimal(r[4])) for r in rows]


async def costs_by_month(conn: AsyncConnection, start: date, end: date) -> list[tuple[date, Cost]]:
    """Chargeable-or-not provider cost per month and SKU (through the vendor's SKU), with the confidence of the rate that priced it."""
    rows = (
        await conn.execute(
            text(
                "SELECT date_trunc('month', e.occurred_at)::date, v.sku_id, e.cost_currency, sum(e.cost), "
                "min(CASE WHEN r.confidence = 'SIGNED_TARIFF' THEN 'SIGNED_TARIFF' ELSE coalesce(r.confidence, 'NONE') END) "
                "FROM commercial.vendor_cost_event e JOIN commercial.vendor v ON v.vendor_id = e.vendor_id "
                "LEFT JOIN commercial.vendor_rate r ON r.rate_id = e.rate_id "
                "WHERE e.occurred_at >= :s AND e.occurred_at < (CAST(:e AS date) + interval '1 month') "
                "GROUP BY 1, 2, 3, CASE WHEN r.confidence = 'SIGNED_TARIFF' THEN 1 ELSE 0 END"
            ),
            {"s": start, "e": end},
        )
    ).all()
    return [(r[0], Cost(r[1], r[2], Decimal(r[3]), None if r[4] == "NONE" else r[4])) for r in rows]
