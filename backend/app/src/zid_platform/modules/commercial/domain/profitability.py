"""Profitability, run-rate and retention (ZID-COM-WF-2026-11 CW-13). Pure.

Three honest calculations and nothing speculative:

- margin per SKU and currency: what was rated, minus what providers cost, in the SAME currency. A cost in another currency is listed, never converted (no FX source
  is approved), and a cost that is only a quote or an estimate is flagged as inferred, never presented as actual;
- run-rate: the average of the last complete months, times twelve. No growth is assumed;
- retention: how much of last month's revenue is still there this month, split into churn, contraction and expansion, with gross and net retention.

Nothing here prices a service or changes any record.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal

ZERO = Decimal(0)
_CENT = Decimal("0.01")


def _q(v: Decimal) -> Decimal:
    return v.quantize(_CENT, rounding=ROUND_HALF_EVEN)


@dataclass(frozen=True, slots=True)
class Cost:
    sku: str
    currency: str
    amount: Decimal
    # SIGNED_TARIFF is a contracted rate; anything else (QUOTED, ESTIMATE, or no rate on record) is inferred
    confidence: str | None


def margin(
    revenue: Mapping[tuple[str, str], Decimal], costs: list[Cost]
) -> dict[str, list[dict[str, object]]]:
    """Margin by (sku, currency). `revenue` is keyed (sku, currency). A SKU with revenue and no cost in that currency shows its margin as unknown, not as 100%."""
    cost_by: dict[tuple[str, str], Decimal] = {}
    inferred: dict[tuple[str, str], bool] = {}
    for c in costs:
        k = (c.sku, c.currency)
        cost_by[k] = cost_by.get(k, ZERO) + c.amount
        inferred[k] = inferred.get(k, False) or c.confidence != "SIGNED_TARIFF"
    lines: list[dict[str, object]] = []
    for (sku, cur), rev in sorted(revenue.items()):
        known = (sku, cur) in cost_by
        cost = cost_by.get((sku, cur), ZERO)
        gm = rev - cost
        lines.append(
            {
                "sku": sku,
                "currency": cur,
                "revenue": str(_q(rev)),
                "cost": str(_q(cost)) if known else None,
                "margin": str(_q(gm)) if known else None,
                "margin_pct": str(_q(gm / rev * 100)) if known and rev > 0 else None,
                "cost_is_inferred": inferred.get((sku, cur), False) if known else None,
            }
        )
    unmatched = [
        {"sku": sku, "currency": cur, "cost": str(_q(v)), "inferred": inferred[(sku, cur)]}
        for (sku, cur), v in sorted(cost_by.items())
        if (sku, cur) not in revenue
    ]
    return {"lines": lines, "cost_without_revenue_in_that_currency": unmatched}


def run_rate(series: Mapping[date, Decimal], *, as_of: date, window: int = 3) -> dict[str, object]:
    """The average of up to `window` complete months before `as_of`'s month, times twelve. A series with fewer months than the window says so instead of guessing."""
    this_month = as_of.replace(day=1)
    complete = sorted((m, v) for m, v in series.items() if m < this_month)[-window:]
    if not complete:
        return {
            "months_used": 0,
            "monthly_average": None,
            "annualised": None,
            "complete_window": False,
        }
    avg = sum((v for _, v in complete), ZERO) / len(complete)
    return {
        "months_used": len(complete),
        "monthly_average": str(_q(avg)),
        "annualised": str(_q(avg * 12)),
        "complete_window": len(complete) == window,
    }


def retention(previous: Mapping[str, Decimal], current: Mapping[str, Decimal]) -> dict[str, object]:
    """Revenue retention between two months, by company. New companies add to neither retention figure."""
    churn = contraction = expansion = new = ZERO
    churned = new_n = 0
    for t, was in previous.items():
        now = current.get(t, ZERO)
        if was <= 0:
            continue
        if now <= 0:
            churn += was
            churned += 1
        elif now < was:
            contraction += was - now
        else:
            expansion += now - was
    for t, now in current.items():
        if previous.get(t, ZERO) <= 0 and now > 0:
            new += now
            new_n += 1
    base = sum((v for v in previous.values() if v > 0), ZERO)
    gross = (base - churn - contraction) / base * 100 if base > 0 else None
    net = (base - churn - contraction + expansion) / base * 100 if base > 0 else None
    return {
        "previous_revenue": str(_q(base)),
        "churned": {"companies": churned, "revenue": str(_q(churn))},
        "contraction": str(_q(contraction)),
        "expansion": str(_q(expansion)),
        "new": {"companies": new_n, "revenue": str(_q(new))},
        "gross_retention_pct": str(_q(gross)) if gross is not None else None,
        "net_retention_pct": str(_q(net)) if net is not None else None,
    }
