"""Vendor costs: suppliers, rates, routes, per-attempt costs, invoice reconciliation (ZID-COM-001 §8; CR-029).

`record_check` runs inside the verification transaction that received the provider's result. Like the billing
recorder it never raises: a cost-ledger failure is counted and logged and the verification carries on.
"""

from __future__ import annotations

import logging
import uuid
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.commercial.application.change_log import log_change
from zid_platform.modules.commercial.domain import vendor as v

log = logging.getLogger("zid.commercial.vendor")
STATS: Counter[str] = Counter()


def _d(x: Any) -> str | None:
    return None if x is None else format(Decimal(x).normalize(), "f")


async def register(
    conn: AsyncConnection,
    *,
    vendor_id: str,
    display_name: str,
    legal_role: str,
    adapter_id: str | None,
    sku_id: str,
    spike_threshold_pct: Decimal | None,
    actor: str,
) -> None:
    await conn.execute(
        text(
            "INSERT INTO commercial.vendor (vendor_id, display_name, legal_role, adapter_id, sku_id, "
            "spike_threshold_pct, created_by) VALUES (:v, :n, :r, :a, :s, :t, :b)"
        ),
        {
            "v": vendor_id,
            "n": display_name,
            "r": legal_role,
            "a": adapter_id,
            "s": sku_id,
            "t": spike_threshold_pct,
            "b": uuid.UUID(actor),
        },
    )


async def set_recognition(conn: AsyncConnection, vendor_id: str, basis: str) -> None:
    """Finance's principal-or-agent decision for this supplier's checks (§8)."""
    r = await conn.execute(
        text("UPDATE commercial.vendor SET recognition_basis = :b WHERE vendor_id = :v"),
        {"b": basis, "v": vendor_id},
    )
    if not r.rowcount:
        raise v.VendorError("VENDOR_NOT_FOUND")


async def set_route(
    conn: AsyncConnection,
    vendor_id: str,
    check_type: str,
    country: str,
    *,
    status: str,
    reason: str,
    actor: str | None,
) -> None:
    await conn.execute(
        text(
            "INSERT INTO commercial.vendor_route (vendor_id, check_type, country, status, reason, changed_by) "
            "VALUES (:v, :c, :k, :s, :r, :b) ON CONFLICT (vendor_id, check_type, country) DO UPDATE SET "
            "status = EXCLUDED.status, reason = EXCLUDED.reason, changed_by = EXCLUDED.changed_by, changed_at = now()"
        ),
        {
            "v": vendor_id,
            "c": check_type,
            "k": country,
            "s": status,
            "r": reason,
            "b": uuid.UUID(actor) if actor else None,
        },
    )


async def record_rate(
    conn: AsyncConnection,
    *,
    vendor_id: str,
    check_type: str,
    country: str,
    base_price: Decimal,
    retry_price: Decimal,
    currency: str,
    confidence: str,
    source_ref: str,
    actor: str,
    modality: str = "STANDARD",
    now: datetime | None = None,
) -> dict[str, Any]:
    """A new rate supersedes the current one. A rise above the vendor's spike threshold puts the route in
    NEEDS_APPROVAL: new quotes cannot promise it until someone approves the route again."""
    now = now or datetime.now(UTC)
    v.check_rate(base_price, retry_price, confidence)
    vendor = (
        await conn.execute(
            text("SELECT spike_threshold_pct FROM commercial.vendor WHERE vendor_id = :v"),
            {"v": vendor_id},
        )
    ).first()
    if vendor is None:
        raise v.VendorError("VENDOR_NOT_FOUND")
    prior = (
        await conn.execute(
            text(
                "SELECT rate_id, base_price FROM commercial.vendor_rate WHERE vendor_id = :v AND check_type = :c "
                "AND country = :k AND modality = :m AND superseded_at IS NULL FOR UPDATE"
            ),
            {"v": vendor_id, "c": check_type, "k": country, "m": modality},
        )
    ).first()
    if prior is not None:
        await conn.execute(
            text("UPDATE commercial.vendor_rate SET superseded_at = :n WHERE rate_id = :r"),
            {"n": now, "r": prior[0]},
        )
    rid = uuid.uuid4()
    await conn.execute(
        text(
            "INSERT INTO commercial.vendor_rate (rate_id, vendor_id, check_type, country, modality, base_price, "
            "retry_price, currency, confidence, source_ref, valid_from, recorded_by) VALUES (:r, :v, :c, :k, :m, :b, "
            ":rt, :cur, :cf, :s, :n, :a)"
        ),
        {
            "r": rid,
            "v": vendor_id,
            "c": check_type,
            "k": country,
            "m": modality,
            "b": base_price,
            "rt": retry_price,
            "cur": currency,
            "cf": confidence,
            "s": source_ref,
            "n": now,
            "a": uuid.UUID(actor),
        },
    )
    spike = v.is_spike(Decimal(prior[1]) if prior else None, base_price, vendor[0])
    current = (
        await conn.execute(
            text(
                "SELECT status FROM commercial.vendor_route WHERE vendor_id = :v AND check_type = :c AND country = :k"
            ),
            {"v": vendor_id, "c": check_type, "k": country},
        )
    ).scalar()
    if spike:
        await set_route(
            conn,
            vendor_id,
            check_type,
            country,
            status="NEEDS_APPROVAL",
            reason="RATE_SPIKE",
            actor=actor,
        )
        status = "NEEDS_APPROVAL"
    elif current is None:
        await set_route(
            conn, vendor_id, check_type, country, status="ACTIVE", reason="FIRST_RATE", actor=actor
        )
        status = "ACTIVE"
    else:
        status = str(current)
    await log_change(
        conn,
        kind="VENDOR_RATE_RECORDED",
        ref=str(rid),
        tenant_id=None,
        actor=actor,
        detail={
            "vendor": vendor_id,
            "check_type": check_type,
            "country": country,
            "base_price": str(base_price),
            "confidence": confidence,
            "route_status": status,
        },
    )
    return {"rate_id": str(rid), "route_status": status, "spike": spike}


async def record_check(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    adapter_id: str,
    supplier_ref: str,
    check_type: str,
    country: str,
    decision_status: str,
    reasons: Sequence[str],
    subject_ref: str | None,
    at: datetime | None = None,
) -> bool:
    """Record what one provider attempt cost. Never raises; returns True when a cost row was written."""
    at = at or datetime.now(UTC)
    try:
        async with conn.begin_nested():
            vendor = (
                await conn.execute(
                    text("SELECT vendor_id FROM commercial.vendor WHERE adapter_id = :a"),
                    {"a": adapter_id},
                )
            ).scalar()
            if vendor is None:
                STATS["no_vendor"] += 1
                return False
            rate_row = (
                (
                    await conn.execute(
                        text(
                            "SELECT rate_id, base_price, retry_price, currency, confidence FROM commercial.vendor_rate "
                            "WHERE vendor_id = :v AND check_type = :c AND country = :k AND modality = 'STANDARD' "
                            "AND superseded_at IS NULL"
                        ),
                        {"v": vendor, "c": check_type, "k": country},
                    )
                )
                .mappings()
                .first()
            )
            attempt = 1 + int(
                (
                    await conn.execute(
                        text(
                            "SELECT count(*) FROM commercial.vendor_cost_event WHERE vendor_id = :v "
                            "AND tenant_id = :t AND subject_ref = :s"
                        ),
                        {"v": vendor, "t": uuid.UUID(tenant_id), "s": subject_ref},
                    )
                ).scalar_one()
            )
            outcome = v.classify(decision_status, reasons)
            rate = (
                v.Rate(
                    Decimal(rate_row["base_price"]),
                    Decimal(rate_row["retry_price"]),
                    rate_row["currency"],
                    rate_row["confidence"],
                )
                if rate_row
                else v.Rate(
                    Decimal(0), Decimal(0), "USD", "ESTIMATE"
                )  # unpriced: visible in reconciliation
            )
            priced = v.price_attempt(rate, outcome, attempt)
            r = await conn.execute(
                text(
                    "INSERT INTO commercial.vendor_cost_event (cost_id, tenant_id, vendor_id, supplier_ref, check_type, "
                    "country, outcome, attempt_no, cost, cost_currency, rate_id, chargeable, charge_basis, subject_ref, "
                    "occurred_at) VALUES (:i, :t, :v, :s, :c, :k, :o, :n, :cost, :cur, :r, :ch, :cb, :sub, :at) "
                    "ON CONFLICT (vendor_id, supplier_ref) DO NOTHING"
                ),
                {
                    "i": uuid.uuid4(),
                    "t": uuid.UUID(tenant_id),
                    "v": vendor,
                    "s": supplier_ref,
                    "c": check_type,
                    "k": country,
                    "o": outcome,
                    "n": attempt,
                    "cost": priced.cost,
                    "cur": rate.currency,
                    "r": rate_row["rate_id"] if rate_row else None,
                    "ch": priced.chargeable,
                    "cb": priced.charge_basis,
                    "sub": subject_ref,
                    "at": at,
                },
            )
            written = bool(r.rowcount)
    except Exception:  # the cost ledger never blocks a verification
        STATS["write_failed"] += 1
        log.exception("VENDOR_COST_WRITE_FAILED adapter=%s", adapter_id)
        return False
    STATS["recorded" if written else "duplicate"] += 1
    return written


async def import_invoice(
    conn: AsyncConnection,
    *,
    vendor_id: str,
    invoice_ref: str,
    lines: Sequence[tuple[str, Decimal, str]],
    actor: str,
) -> int:
    for ref, amount, currency in lines:
        await conn.execute(
            text(
                "INSERT INTO commercial.vendor_invoice_line (line_id, vendor_id, invoice_ref, supplier_ref, amount, "
                "currency, imported_by) VALUES (:i, :v, :r, :s, :a, :c, :b)"
            ),
            {
                "i": uuid.uuid4(),
                "v": vendor_id,
                "r": invoice_ref,
                "s": ref,
                "a": amount,
                "c": currency,
                "b": uuid.UUID(actor),
            },
        )
    return len(lines)


async def reconcile_invoice(
    conn: AsyncConnection,
    *,
    vendor_id: str,
    invoice_ref: str,
    period_start: datetime,
    period_end: datetime,
) -> dict[str, Any]:
    """Join one supplier invoice to the cost events of its period. Every finding becomes an open dispute."""
    costs = {
        str(r[0]): Decimal(r[1])
        for r in (
            await conn.execute(
                text(
                    "SELECT supplier_ref, cost FROM commercial.vendor_cost_event WHERE vendor_id = :v "
                    "AND occurred_at >= :s AND occurred_at < :e"
                ),
                {"v": vendor_id, "s": period_start, "e": period_end},
            )
        ).all()
    }
    invoice = [
        (str(r[0]), Decimal(r[1]))
        for r in (
            await conn.execute(
                text(
                    "SELECT supplier_ref, amount FROM commercial.vendor_invoice_line WHERE vendor_id = :v "
                    "AND invoice_ref = :r ORDER BY imported_at, line_id"
                ),
                {"v": vendor_id, "r": invoice_ref},
            )
        ).all()
    ]
    rec = v.reconcile(costs, invoice)
    findings: list[tuple[str, str, Decimal | None, Decimal | None]] = [
        *(("AMOUNT_MISMATCH", ref, ours, theirs) for ref, ours, theirs in rec.amount_mismatch),
        *(("MISSING_ON_INVOICE", ref, costs[ref], None) for ref in rec.missing_on_invoice),
        *(("UNKNOWN_ON_INVOICE", ref, None, dict(invoice)[ref]) for ref in rec.unknown_on_invoice),
        *(
            ("DUPLICATE_ON_INVOICE", ref, costs.get(ref), dict(invoice)[ref])
            for ref in rec.duplicate_on_invoice
        ),
    ]
    for kind, ref, ours, theirs in findings:
        await conn.execute(
            text(
                "INSERT INTO commercial.vendor_dispute (dispute_id, vendor_id, invoice_ref, supplier_ref, kind, ours, "
                "theirs) VALUES (:i, :v, :r, :s, :k, :o, :t) ON CONFLICT DO NOTHING"
            ),
            {
                "i": uuid.uuid4(),
                "v": vendor_id,
                "r": invoice_ref,
                "s": ref,
                "k": kind,
                "o": ours,
                "t": theirs,
            },
        )
    return {
        "matched": len(rec.matched),
        "amount_mismatch": len(rec.amount_mismatch),
        "missing_on_invoice": len(rec.missing_on_invoice),
        "unknown_on_invoice": len(rec.unknown_on_invoice),
        "duplicate_on_invoice": len(rec.duplicate_on_invoice),
        "clean": not findings,
    }


async def confirmed_skus(conn: AsyncConnection) -> frozenset[str]:
    """Pass-through SKUs a quote may promise: an active supplier with an ACTIVE route on a SIGNED_TARIFF rate."""
    rows = (
        await conn.execute(
            text(
                "SELECT DISTINCT v.sku_id FROM commercial.vendor v "
                "JOIN commercial.vendor_route r ON r.vendor_id = v.vendor_id AND r.status = 'ACTIVE' "
                "JOIN commercial.vendor_rate t ON t.vendor_id = r.vendor_id AND t.check_type = r.check_type "
                "AND t.country = r.country AND t.superseded_at IS NULL AND t.confidence = 'SIGNED_TARIFF' "
                "WHERE v.status = 'ACTIVE'"
            )
        )
    ).scalars()
    return frozenset(rows)


async def cost_report(
    conn: AsyncConnection, period_start: datetime, period_end: datetime
) -> list[dict[str, Any]]:
    """Per vendor and country: attempts, chargeable attempts and supplier cost. Margin needs billed revenue,
    which appears once verification SKUs carry a price; until then it is reported as None, never invented."""
    rows = (
        await conn.execute(
            text(
                "SELECT vendor_id, country, count(*), count(*) FILTER (WHERE chargeable), sum(cost), "
                "count(*) FILTER (WHERE rate_id IS NULL) FROM commercial.vendor_cost_event "
                "WHERE occurred_at >= :s AND occurred_at < :e GROUP BY vendor_id, country ORDER BY 1, 2"
            ),
            {"s": period_start, "e": period_end},
        )
    ).all()
    return [
        {
            "vendor": r[0],
            "country": r[1],
            "attempts": int(r[2]),
            "chargeable_attempts": int(r[3]),
            "vendor_cost": _d(r[4]),
            "unpriced_attempts": int(r[5]),
            "margin": None,
        }
        for r in rows
    ]
