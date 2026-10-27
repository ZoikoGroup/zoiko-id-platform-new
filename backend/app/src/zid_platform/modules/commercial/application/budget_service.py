"""Bill preview, budgets and threshold alerts (ZID-COM-001 §7; CR-029).

The preview rates month-to-date usage with the invoice's own rating function and, after the first six hours,
a projection of the whole month. Evaluation records each threshold reached once per month and emits
`commercial.budget.threshold_crossed`, which the alerts centre shows as a mandatory notice. Nothing here can
throttle or refuse anything.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection
from zid_outbox import emit

from zid_platform.modules.commercial.application import rating_service
from zid_platform.modules.commercial.domain import billable as b
from zid_platform.modules.commercial.domain import budgets as bg
from zid_platform.modules.commercial.domain.rating import RatingError, Segment, rate

ALERT_EVENT = "commercial.budget.threshold_crossed"


def _d(v: Decimal | None) -> str | None:
    return None if v is None else format(v.normalize(), "f")


async def _segments(conn: AsyncConnection, month: date) -> tuple[list[Segment], str]:
    out = []
    currencies = set()
    for plan, s, e, bk in await rating_service.segments_for(conn, month):
        currencies.add(bk["currency"])
        out.append(
            Segment(
                await rating_service.terms_for(conn, plan),
                s,
                e,
                await rating_service.prices_for(conn, str(bk["price_book_id"]), plan),
            )
        )
    if len(currencies) != 1:
        raise RatingError("CURRENCY_MIXED")
    return out, currencies.pop()


async def preview(
    conn: AsyncConnection, *, connections: Mapping[str, int], now: datetime | None = None
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    month = b.window_month(now)
    start, end = rating_service.bounds(month)
    try:
        segments, currency = await _segments(conn, month)
    except RatingError as e:
        return {
            "month": f"{month:%Y-%m}",
            "status": "UNPRICED",
            "reason": e.code,
            "assumptions": [],
        }
    usage = await rating_service.usage_for(conn, month, [(s.start, s.end) for s in segments])
    mtd = rate(
        currency=currency,
        period_start=start,
        period_end=end,
        segments=segments,
        usage=usage,
        connections=connections,
    )
    fraction = bg.elapsed_fraction(start, end, now)
    status = bg.projection_status(start, now)
    projected = None
    if status != "NONE":
        projected = rate(
            currency=currency,
            period_start=start,
            period_end=end,
            segments=segments,
            usage=[bg.project_usage(u, fraction) for u in usage],
            connections=connections,
        )
    totals: dict[str, Decimal] = {}
    for u in usage:
        for m, q in u.items():
            totals[m] = totals.get(m, Decimal(0)) + q
    return {
        "month": f"{month:%Y-%m}",
        "status": "ESTIMATE",
        "projection_confidence": status,
        "elapsed_share": _d(fraction.quantize(Decimal("0.0001"))),
        "currency": currency,
        "usage_to_date": {m: _d(q) for m, q in sorted(totals.items())},
        "bill_to_date": _d(mtd.subtotal),
        "projected_bill": _d(projected.subtotal) if projected else None,
        "lines_to_date": [
            {"kind": ln.kind, "meter": ln.meter, "formula": ln.formula, "net": _d(ln.net)}
            for ln in mtd.lines
        ],
        "warnings": list(mtd.warnings),
        "assumptions": [
            "An estimate, not a quotation or an invoice.",
            "Usage so far is scaled by the share of the month that has passed; no projection in the first six hours.",
            "Taxes and third-party verification checks are excluded.",
            "Extra connections are counted now; the invoice counts them at the month's cut-off.",
        ],
        "_allowances": {m: q for m, q in (segments[-1].plan.included.items())},
        "_totals": totals,
        "_bill": mtd.subtotal,
    }


async def budgets(conn: AsyncConnection) -> list[dict[str, Any]]:
    rows = (
        await conn.execute(text("SELECT * FROM commercial.budget WHERE active ORDER BY created_at"))
    ).mappings()
    return [
        {
            "budget_id": str(r["budget_id"]),
            "meter": r["meter_id"],
            "monthly_limit": _d(Decimal(r["monthly_limit"])),
            "currency": r["currency"],
            "thresholds": list(r["thresholds"]),
        }
        for r in rows
    ]


async def set_budget(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    meter: str | None,
    monthly_limit: Decimal,
    currency: str | None,
    thresholds: Sequence[int],
    actor: str,
) -> dict[str, Any]:
    """Replace the tenant's active budget for this target (the old one stays, inactive)."""
    if meter is not None and meter not in b.METERS:
        raise bg.BudgetError("METER_UNKNOWN")
    if (meter is None) == (currency is None):
        raise bg.BudgetError(
            "BUDGET_TARGET_INVALID", "a bill budget names a currency; a meter budget does not"
        )
    if monthly_limit < 0:
        raise bg.BudgetError("BUDGET_LIMIT_INVALID")
    ts = bg.check_thresholds(thresholds)
    await conn.execute(
        text(
            "UPDATE commercial.budget SET active = false WHERE active AND coalesce(meter_id, 'BILL') = :k"
        ),
        {"k": meter or "BILL"},
    )
    bid = uuid.uuid4()
    await conn.execute(
        text(
            "INSERT INTO commercial.budget (budget_id, tenant_id, meter_id, monthly_limit, currency, thresholds, "
            "created_by) VALUES (:b, :t, :m, :l, :c, :th, :a)"
        ),
        {
            "b": bid,
            "t": uuid.UUID(tenant_id),
            "m": meter,
            "l": monthly_limit,
            "c": currency,
            "th": list(ts),
            "a": uuid.UUID(actor),
        },
    )
    return {
        "budget_id": str(bid),
        "meter": meter,
        "monthly_limit": _d(monthly_limit),
        "thresholds": list(ts),
    }


async def _raise(
    conn: AsyncConnection,
    tenant_id: str,
    key: str,
    month: date,
    threshold: int,
    observed: Decimal,
    limit: Decimal,
) -> bool:
    r = await conn.execute(
        text(
            "INSERT INTO commercial.budget_alert (tenant_id, alert_key, period, threshold, observed, limit_value) "
            "VALUES (:t, :k, :p, :h, :o, :l) ON CONFLICT DO NOTHING"
        ),
        {
            "t": uuid.UUID(tenant_id),
            "k": key,
            "p": month,
            "h": threshold,
            "o": observed,
            "l": limit,
        },
    )
    if not r.rowcount:
        return False
    await emit(
        conn,
        name=ALERT_EVENT,
        tenant_id=tenant_id,
        aggregate_type="budget_alert",
        aggregate_id=f"{tenant_id}:{key}:{month:%Y%m}:{threshold}",  # unique across tenants
        aggregate_version=1,
        data={"key": key, "month": f"{month:%Y-%m}", "threshold": threshold},
    )
    return True


async def evaluate(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    connections: Mapping[str, int],
    now: datetime | None = None,
) -> dict[str, Any]:
    """Record every threshold reached this month (once each) for budgets and included allowances."""
    now = now or datetime.now(UTC)
    p = await preview(conn, connections=connections, now=now)
    if p["status"] != "ESTIMATE":
        return {"raised": [], "status": p["status"]}
    month = b.window_month(now)
    raised: list[dict[str, Any]] = []
    totals: dict[str, Decimal] = p["_totals"]
    for budget in await budgets(conn):
        limit = Decimal(budget["monthly_limit"])
        observed = (
            p["_bill"] if budget["meter"] is None else totals.get(budget["meter"], Decimal(0))
        )
        key = f"BUDGET:{budget['meter'] or 'BILL'}"
        for t in bg.crossed(observed, limit, budget["thresholds"]):
            if await _raise(conn, tenant_id, key, month, t, observed, limit):
                raised.append({"key": key, "threshold": t})
    for meter, hit in bg.quota_crossings(totals, p["_allowances"]).items():
        allowance = p["_allowances"][meter]
        for t in hit:
            if await _raise(conn, tenant_id, f"QUOTA:{meter}", month, t, totals[meter], allowance):
                raised.append({"key": f"QUOTA:{meter}", "threshold": t})
    return {"raised": raised, "status": "EVALUATED"}


def public(preview_out: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in preview_out.items() if not k.startswith("_")}
