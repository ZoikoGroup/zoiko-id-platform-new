"""Rating runs: assemble a frozen month's inputs, rate them, store the lines (ZID-COM-001 §13; CR-026).

A run is allowed only on a month whose meters are all frozen, so nothing can still change underneath it.
Re-running with identical inputs must give identical outputs (the replay proof): it returns the existing run,
and a mismatch is raised as `RATING_NONDETERMINISM`, never overwritten. Different inputs (a new credit, an
approved adjustment) supersede the previous run, whose lines stay as history.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.commercial.domain import billable as b
from zid_platform.modules.commercial.domain.rating import (
    INCLUDED_FIELD,
    Credit,
    PlanTerms,
    PriceLine,
    RatedPeriod,
    RatingError,
    Segment,
    rate,
)
from zid_platform.modules.commercial.infrastructure import billable_store, catalogue_store

DEFAULT_PLAN = "DEVELOPER"


def bounds(month: date) -> tuple[datetime, datetime]:
    nxt = b.next_month(month)
    return (
        datetime(month.year, month.month, 1, tzinfo=UTC),
        datetime(nxt.year, nxt.month, 1, tzinfo=UTC),
    )


def _digest(obj: Any) -> str:
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def _d(v: Decimal | None) -> str | None:
    return None if v is None else format(v.normalize(), "f")


async def terms_for(conn: AsyncConnection, plan_code: str) -> PlanTerms:
    p = await catalogue_store.plan(conn, plan_code)
    if p is None:
        raise RatingError("PLAN_UNKNOWN", plan_code)
    return PlanTerms(
        plan_code,
        Decimal(p["platform_fee"]),
        bool(p["fee_is_floor"]),
        {m: (Decimal(p[f]) if p[f] is not None else None) for m, f in INCLUDED_FIELD.items()},
        p["sso_included"],
    )


async def prices_for(
    conn: AsyncConnection, price_book_id: str, plan_code: str
) -> dict[str, PriceLine]:
    return {
        ln["sku_id"]: PriceLine(
            Decimal(ln["unit_rate"]),
            int(ln["unit_divisor"]),
            Decimal(ln["minimum_amount"]) if ln["minimum_amount"] is not None else None,
        )
        for ln in await catalogue_store.lines(conn, price_book_id)
        if ln["plan_code"] == plan_code
    }


async def _book_at(conn: AsyncConnection, at: datetime) -> dict[str, Any]:
    row = (
        (
            await conn.execute(
                text(
                    "SELECT * FROM commercial.price_book WHERE status IN ('APPROVED', 'SUPERSEDED') "
                    "AND effective_from <= :a ORDER BY version DESC LIMIT 1"
                ),
                {"a": at},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise RatingError("NO_PRICE_BOOK", "no price book was approved for this period")
    return dict(row)


async def segments_for(
    conn: AsyncConnection, month: date
) -> list[tuple[str, datetime, datetime, dict[str, Any]]]:
    """(plan, start, end, price book) covering the month, from the tenant's entitlement history; any gap is
    the default plan. A segment keeps the price book its entitlement was signed under, so a later book never
    reprices it."""
    start, end = bounds(month)
    rows = (
        (
            await conn.execute(
                text(
                    "SELECT plan_code, price_book_id, effective_from, effective_to FROM commercial.entitlement "
                    "WHERE effective_from < :e AND (effective_to IS NULL OR effective_to > :s) "
                    "ORDER BY effective_from"
                ),
                {"s": start, "e": end},
            )
        )
        .mappings()
        .all()
    )
    out: list[tuple[str, datetime, datetime, dict[str, Any]]] = []
    cursor = start
    for r in rows:
        s = max(r["effective_from"], start)
        e = min(r["effective_to"] or end, end)
        if s > cursor:
            out.append((DEFAULT_PLAN, cursor, s, await _book_at(conn, cursor)))
        book = (
            await catalogue_store.price_book(conn, str(r["price_book_id"]))
            if r["price_book_id"]
            else await _book_at(conn, s)
        )
        assert book is not None
        out.append((str(r["plan_code"]), s, e, book))
        cursor = e
    if cursor < end:
        out.append((DEFAULT_PLAN, cursor, end, await _book_at(conn, cursor)))
    return out


async def usage_for(
    conn: AsyncConnection, month: date, bounds: Sequence[tuple[datetime, datetime]]
) -> list[dict[str, Decimal]]:
    """Per segment: billable quantity by meter in this month's window. A late event (earned before the month,
    recorded in it) counts in the first segment; approved adjustments count in the last."""
    usage: list[dict[str, Decimal]] = []
    last = len(bounds) - 1
    for i, (s, e) in enumerate(bounds):
        lo = "occurred_at >= :s" if i > 0 else "TRUE"
        hi = "occurred_at < :e" if i < last else "TRUE"
        rows = (
            await conn.execute(
                text(
                    "SELECT meter_id, sum(quantity) FROM commercial.billable_event "
                    f"WHERE window_month = :w AND {lo} AND {hi} GROUP BY meter_id"
                ),
                {"w": month, "s": s, "e": e},
            )
        ).all()
        usage.append({r[0]: Decimal(r[1]) for r in rows})
    for meter, delta in (await billable_store.adjustment_totals(conn, month)).items():
        usage[last][meter] = max(Decimal(0), usage[last].get(meter, Decimal(0)) + delta)
    return usage


def outputs_hash(rated: RatedPeriod) -> str:
    return _digest(
        {
            "currency": rated.currency,
            "subtotal": _d(rated.subtotal),
            "credit_balance": _d(rated.credit_balance),
            "lines": [
                [
                    ln.kind,
                    ln.sku,
                    ln.meter,
                    ln.plan_code,
                    ln.segment,
                    _d(ln.included),
                    _d(ln.chargeable),
                    _d(ln.unit_rate),
                    ln.unit_divisor,
                    _d(ln.units),
                    _d(ln.net),
                    ln.reverses,
                ]
                for ln in rated.lines
            ],
        }
    )


async def run(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    month: date,
    connections: dict[str, int],
    credits: Sequence[Credit] = (),
    actor: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    windows = {w["meter_id"]: w for w in await billable_store.windows(conn, month)}
    missing = [m for m in b.METERS if m not in windows]
    if missing:
        raise RatingError("WINDOW_NOT_FROZEN", f"freeze the month first: {', '.join(missing)}")
    start, end = bounds(month)
    plan_segments = await segments_for(conn, month)
    books = {str(bk["price_book_id"]): bk for _, _, _, bk in plan_segments}
    currencies = {bk["currency"] for bk in books.values()}
    if len(currencies) != 1:
        raise RatingError("CURRENCY_MIXED", "one period is rated in one currency")
    currency = currencies.pop()
    segments = [
        Segment(
            await terms_for(conn, plan),
            s,
            e,
            await prices_for(conn, str(bk["price_book_id"]), plan),
        )
        for plan, s, e, bk in plan_segments
    ]
    usage = await usage_for(conn, month, [(s.start, s.end) for s in segments])
    overrides = await catalogue_store.overrides(conn, end)
    extras = {
        cap: sum(o.extra_count or 0 for o in overrides if o.capability_key == cap)
        for cap in ("saml_sso", "scim")
    }
    inputs = {
        "month": month.isoformat(),
        "windows": {m: windows[m]["hash_total"] for m in sorted(windows)},
        "adjustments": {
            m: _d(q)
            for m, q in sorted((await billable_store.adjustment_totals(conn, month)).items())
        },
        "segments": [
            [plan, s.isoformat(), e.isoformat(), bk["version"]] for plan, s, e, bk in plan_segments
        ],
        "connections": dict(sorted(connections.items())),
        "connection_extras": extras,
        "credits": [[_d(c.amount), c.reason_code, c.reverses] for c in credits],
    }
    inputs_hash = _digest(inputs)
    rated = rate(
        currency=currency,
        period_start=start,
        period_end=end,
        segments=segments,
        usage=usage,
        connections=connections,
        connection_extras=extras,
        credits=credits,
    )
    out_hash = outputs_hash(rated)

    current = (
        (
            await conn.execute(
                text(
                    "SELECT run_id, attempt, inputs_hash, outputs_hash FROM commercial.rating_run "
                    "WHERE period = :p AND state = 'COMPUTED' FOR UPDATE"
                ),
                {"p": month},
            )
        )
        .mappings()
        .first()
    )
    if current is not None and current["inputs_hash"] == inputs_hash:
        if current["outputs_hash"] != out_hash:
            raise RatingError(
                "RATING_NONDETERMINISM",
                "the same inputs rated differently; the stored run is kept and this is an incident",
            )
        return {
            "run_id": str(current["run_id"]),
            "attempt": current["attempt"],
            "reused": True,
            **_view(rated, inputs_hash, out_hash),
        }
    attempt = int(
        (
            await conn.execute(
                text(
                    "SELECT coalesce(max(attempt), 0) + 1 FROM commercial.rating_run WHERE period = :p"
                ),
                {"p": month},
            )
        ).scalar_one()
    )
    if current is not None:
        await conn.execute(
            text("UPDATE commercial.rating_run SET state = 'SUPERSEDED' WHERE run_id = :r"),
            {"r": current["run_id"]},
        )
    run_id = uuid.uuid4()
    await conn.execute(
        text(
            "INSERT INTO commercial.rating_run (run_id, tenant_id, period, attempt, state, currency, subtotal, "
            "credit_balance, inputs_hash, outputs_hash, warnings, actor) VALUES (:r, :t, :p, :a, 'COMPUTED', :c, "
            ":s, :b, :ih, :oh, CAST(:w AS jsonb), :x)"
        ),
        {
            "r": run_id,
            "t": uuid.UUID(tenant_id),
            "p": month,
            "a": attempt,
            "c": currency,
            "s": rated.subtotal,
            "b": rated.credit_balance,
            "ih": inputs_hash,
            "oh": out_hash,
            "w": json.dumps(list(rated.warnings)),
            "x": uuid.UUID(actor) if actor else None,
        },
    )
    for seq, ln in enumerate(rated.lines, start=1):
        await conn.execute(
            text(
                "INSERT INTO commercial.rated_usage (line_id, run_id, tenant_id, period, seq, kind, sku, meter, "
                "plan_code, segment, included, chargeable, unit_rate, unit_divisor, units, formula, net, currency, "
                "reverses) VALUES (:i, :r, :t, :p, :q, :k, :sku, :m, :pl, :sg, :inc, :ch, :ur, :ud, :u, :f, :n, "
                ":c, :rv)"
            ),
            {
                "i": uuid.uuid4(),
                "r": run_id,
                "t": uuid.UUID(tenant_id),
                "p": month,
                "q": seq,
                "k": ln.kind,
                "sku": ln.sku,
                "m": ln.meter,
                "pl": ln.plan_code,
                "sg": ln.segment,
                "inc": ln.included,
                "ch": ln.chargeable,
                "ur": ln.unit_rate,
                "ud": ln.unit_divisor,
                "u": ln.units,
                "f": ln.formula,
                "n": ln.net,
                "c": currency,
                "rv": ln.reverses,
            },
        )
    return {
        "run_id": str(run_id),
        "attempt": attempt,
        "reused": False,
        "superseded": str(current["run_id"]) if current else None,
        **_view(rated, inputs_hash, out_hash),
    }


def _view(rated: RatedPeriod, inputs_hash: str, out_hash: str) -> dict[str, Any]:
    return {
        "currency": rated.currency,
        "subtotal": _d(rated.subtotal),
        "credit_balance": _d(rated.credit_balance),
        "warnings": list(rated.warnings),
        "inputs_hash": inputs_hash,
        "outputs_hash": out_hash,
        "lines": [
            {
                "kind": ln.kind,
                "sku": ln.sku,
                "meter": ln.meter,
                "plan": ln.plan_code,
                "segment": ln.segment,
                "formula": ln.formula,
                "net": _d(ln.net),
            }
            for ln in rated.lines
        ],
    }


async def latest(conn: AsyncConnection, month: date) -> dict[str, Any] | None:
    """The tenant's COMPUTED run for a month with its stored lines, or None."""
    r = (
        (
            await conn.execute(
                text(
                    "SELECT * FROM commercial.rating_run WHERE period = :p AND state = 'COMPUTED'"
                ),
                {"p": month},
            )
        )
        .mappings()
        .first()
    )
    if r is None:
        return None
    lines = (
        await conn.execute(
            text("SELECT * FROM commercial.rated_usage WHERE run_id = :r ORDER BY seq"),
            {"r": r["run_id"]},
        )
    ).mappings()
    return {
        "run_id": str(r["run_id"]),
        "attempt": r["attempt"],
        "period": r["period"].isoformat(),
        "currency": r["currency"],
        "subtotal": _d(Decimal(r["subtotal"])),
        "credit_balance": _d(Decimal(r["credit_balance"])),
        "warnings": r["warnings"],
        "inputs_hash": r["inputs_hash"],
        "outputs_hash": r["outputs_hash"],
        "lines": [
            {
                "kind": ln["kind"],
                "sku": ln["sku"],
                "meter": ln["meter"],
                "plan": ln["plan_code"],
                "segment": ln["segment"],
                "formula": ln["formula"],
                "net": _d(Decimal(ln["net"])),
            }
            for ln in lines
        ],
    }
