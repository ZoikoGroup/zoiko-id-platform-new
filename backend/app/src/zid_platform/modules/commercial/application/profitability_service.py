"""The profitability report (ZID-COM-WF-2026-11 CW-13): margin by SKU, run-rate and retention over a range of months. Read only; it prices nothing and changes nothing."""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.commercial.domain import fx as fxrules
from zid_platform.modules.commercial.domain import profitability as pf
from zid_platform.modules.commercial.infrastructure import fx_store
from zid_platform.modules.commercial.infrastructure import profitability_store as store


def _months(start: date, end: date) -> list[date]:
    out, m = [], start
    while m <= end:
        out.append(m)
        m = date(m.year + (m.month == 12), m.month % 12 + 1, 1)
    return out


async def report(conn: AsyncConnection, *, start: date, end: date, as_of: date) -> dict[str, Any]:
    months = _months(start, end)
    revenue = await store.revenue_by_tenant(conn, start, end)
    costs = await store.costs_by_month(conn, start, end)

    by_month_sku: dict[date, dict[tuple[str, str], Decimal]] = defaultdict(dict)
    totals: dict[str, dict[date, Decimal]] = defaultdict(lambda: defaultdict(lambda: pf.ZERO))
    tenants: dict[tuple[str, date], dict[str, Decimal]] = defaultdict(dict)
    for month, tenant, currency, sku, net in revenue:
        key = (sku or "PLATFORM", currency)
        by_month_sku[month][key] = by_month_sku[month].get(key, pf.ZERO) + net
        totals[currency][month] += net
        tenants[(currency, month)][tenant] = tenants[(currency, month)].get(tenant, pf.ZERO) + net

    cost_cur: dict[date, dict[str, Decimal]] = defaultdict(dict)
    for cm, c in costs:
        cost_cur[cm][c.currency] = cost_cur[cm].get(c.currency, pf.ZERO) + c.amount
    rate_set, usd_per_unit = await fx_store.active(conn)
    consolidated = []
    for m in months:
        rev = {cur: s[m] for cur, s in totals.items() if m in s}
        cst = cost_cur.get(m, {})
        if not rev and not cst:
            continue
        rev_usd, cost_usd = fxrules.to_usd(rev, usd_per_unit), fxrules.to_usd(cst, usd_per_unit)
        whole = bool(rev_usd["complete"]) and bool(cost_usd["complete"])
        consolidated.append(
            {
                "month": m.strftime("%Y-%m"),
                "revenue": rev_usd,
                "cost": cost_usd,
                "margin_usd": str(Decimal(str(rev_usd["usd"])) - Decimal(str(cost_usd["usd"])))
                if whole
                else None,
            }
        )
    margins = []
    for m in months:
        month_costs = [c for cm, c in costs if cm == m]
        if m in by_month_sku or month_costs:
            margins.append(
                {"month": m.strftime("%Y-%m"), **pf.margin(by_month_sku.get(m, {}), month_costs)}
            )

    run_rates = {cur: pf.run_rate(series, as_of=as_of) for cur, series in sorted(totals.items())}
    last_two = months[-2:] if len(months) >= 2 else []
    retention = (
        {
            cur: pf.retention(
                tenants.get((cur, last_two[0]), {}), tenants.get((cur, last_two[1]), {})
            )
            for cur in sorted(totals)
        }
        if last_two
        else {}
    )
    return {
        "from": start.strftime("%Y-%m"),
        "to": end.strftime("%Y-%m"),
        "margins": margins,
        "run_rate": run_rates,
        "consolidated_usd": consolidated,
        "fx": {
            "version": rate_set["version"],
            "source_ref": rate_set["source_ref"],
            "as_of": rate_set["as_of"].isoformat(),
        }
        if rate_set
        else None,
        "retention": {
            "between": [m.strftime("%Y-%m") for m in last_two],
            "by_currency": retention,
        },
        "notes": [
            "Amounts in different currencies are added only as US dollars, at the active FX rate set named in `fx`; with none active, or for a currency it does not list, nothing is converted and the gap is named.",
            "A cost marked inferred comes from a quoted or estimated provider rate, not a signed tariff.",
            "Run-rate is the average of the last complete months times twelve; no growth is assumed.",
        ],
    }
