"""Applying Stripe webhook events (CR-040).

The signature has been checked before anything here runs. An event is applied once (its id is the key, held under an
advisory lock so two deliveries of the same event cannot both run); the tenant it belongs to comes from the platform's
own `payment_customer` record, never from the event's contents. What the platform cannot decide alone (a refund, a
dispute, money for an invoice that is no longer collectable) is recorded as NEEDS_REVIEW for a person."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from zid_platform.modules.commercial.application import invoicing_service
from zid_platform.modules.commercial.application.adapters import from_minor_units
from zid_platform.modules.commercial.domain import stripe_webhook as hook


async def _tenants_of(conn: AsyncConnection, customer_ref: str) -> list[str]:
    rows = await conn.execute(
        text(
            "SELECT t.tenant_id FROM commercial.payment_customer c JOIN commercial.account_tenant t "
            "ON t.account_id = c.account_id WHERE c.customer_ref = :c AND t.effective_to IS NULL"
        ),
        {"c": customer_ref},
    )
    return [str(r[0]) for r in rows]


async def _in_tenant(conn: AsyncConnection, tenant_id: str) -> None:
    await conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": tenant_id})


async def _invoice_tenant(conn: AsyncConnection, tenants: list[str], invoice_id: str) -> str | None:
    """The tenant (of this customer's account) whose invoice the event names, or None."""
    try:
        uuid.UUID(invoice_id)
    except ValueError:
        return None
    for t in tenants:
        await _in_tenant(conn, t)
        found = (
            await conn.execute(
                text("SELECT 1 FROM commercial.invoice WHERE invoice_id = :i"),
                {"i": uuid.UUID(invoice_id)},
            )
        ).first()
        if found:
            return t
    await _in_tenant(conn, "")
    return None


async def _activation_paid(
    conn: AsyncConnection, ev: hook.StripeEvent, tenants: list[str]
) -> tuple[str, str | None, str | None, str | None]:
    """A plan-activation payment (CR-062): record it against its order and, once it is paid in full, switch the plan. The same payment may arrive here and through
    the buyer's return in either order; both are safe to repeat."""
    from zid_platform.modules.commercial.application import activation_service as activations
    from zid_platform.modules.commercial.application import orders_service as orders
    from zid_platform.modules.commercial.domain.orders import OrderError

    order_id = str(ev.order_id)
    try:
        uuid.UUID(order_id)
    except ValueError:
        return "UNMATCHED", None, "plan payment with a malformed order reference", None
    tenant, row = None, None
    for (
        t
    ) in tenants:  # only this customer's own companies: an order id from elsewhere is not followed
        found = await activations.load(conn, t, order_id)
        if found is not None:
            tenant, row = t, found
            break
    if tenant is None or row is None:
        return (
            "UNMATCHED",
            None,
            f"no plan payment asked for on order {order_id} for customer",
            None,
        )
    if not (ev.object_id and ev.amount_minor and ev.currency):
        return "UNMATCHED", tenant, "plan payment without an amount", None
    try:
        recorded = await activations.record_payment(
            conn,
            tenant,
            order_id,
            payment_ref=ev.object_id,
            amount_minor=ev.amount_minor,
            currency=ev.currency,
        )
    except activations.ActivationError as e:
        return "NEEDS_REVIEW", tenant, f"plan payment {ev.object_id}: {e.code}", None
    try:
        await orders.activate(
            conn, order_id, actor=str(row["activated_by"]), direct=True, require_payment=True
        )
    except OrderError as e:
        return (
            "NEEDS_REVIEW",
            tenant,
            f"plan payment {ev.object_id} recorded; the order did not switch: {e.code}",
            None,
        )
    return (
        ("IGNORED" if recorded.get("already_recorded") else "APPLIED"),
        tenant,
        ev.object_id,
        None,
    )


async def _apply(
    conn: AsyncConnection, ev: hook.StripeEvent, now: datetime
) -> tuple[str, str | None, str | None, str | None]:
    """(outcome, tenant id, detail, invoice id). The invoice id is returned only once it is shown to belong to the
    customer's account, so a review item never points an operator at somebody else's invoice."""
    tenants = await _tenants_of(conn, ev.customer_ref) if ev.customer_ref else []
    kind = ev.event_type
    if kind.startswith("charge.dispute."):
        # A dispute is a case with an owner and a deadline: it is opened (or moved) here, and its outcome is only ever the processor's.
        from zid_platform.modules.commercial.application import dispute_service

        await _in_tenant(conn, "")
        case_id, _ = await dispute_service.ingest(
            conn,
            event_type=kind,
            obj=ev.data,
            livemode=ev.livemode,
            merchant_ref=ev.merchant_ref,
            now=now,
        )
        return "APPLIED", None, f"dispute case {case_id}", None
    if kind == "charge.refunded" and isinstance(ev.data.get("payment_intent"), str):
        from zid_platform.modules.commercial.application import activation_service

        returned = await activation_service.note_refunded(
            conn, str(ev.data["payment_intent"]), now=now
        )
        if (
            returned
        ):  # the refund of a plan-activation payment: it is never credited to an invoice now
            return "APPLIED", None, f"plan payment refunded for order {returned}", None
    if kind == "charge.refunded":
        from zid_platform.modules.commercial.application import refund_service

        refunds: list[Any] = (ev.data.get("refunds") or {}).get("data") or []
        newest: dict[str, Any] = refunds[0] if refunds and isinstance(refunds[0], dict) else {}
        raw_meta = newest.get("metadata")
        meta: dict[str, Any] = raw_meta if isinstance(raw_meta, dict) else {}
        await _in_tenant(conn, "")
        linked = await refund_service.note_provider_refund(
            conn,
            refund_ref=str(newest["id"]) if newest.get("id") else None,
            case_ref=str(meta["zid_refund"]) if meta.get("zid_refund") else None,
            payment_intent=ev.data.get("payment_intent")
            if isinstance(ev.data.get("payment_intent"), str)
            else None,
            now=now,
        )
        if linked:
            return "APPLIED", None, f"refund case {linked}", None
    if kind in hook.REVIEW_TYPES:
        what = f"{kind} {ev.object_id or ''}".strip()
        tenant = await _invoice_tenant(conn, tenants, ev.invoice_id) if ev.invoice_id else None
        invoice = ev.invoice_id if tenant else None
        if tenant is None and len(tenants) == 1:
            tenant = tenants[0]
        await _in_tenant(conn, "")
        return "NEEDS_REVIEW", tenant, what[:300], invoice
    if kind not in (hook.PAYMENT_SUCCEEDED, hook.PAYMENT_FAILED):
        return "IGNORED", None, None, None
    if ev.order_id and not ev.invoice_id:
        if kind == hook.PAYMENT_SUCCEEDED:
            return await _activation_paid(conn, ev, tenants)
        return (
            "IGNORED",
            None,
            "a plan payment that did not go through; the order keeps waiting",
            None,
        )  # the buyer can pay again
    if not (ev.invoice_id and ev.object_id and ev.amount_minor and ev.currency):
        return "UNMATCHED", None, "payment event without an invoice reference", None
    tenant = await _invoice_tenant(conn, tenants, ev.invoice_id)
    if tenant is None:
        return "UNMATCHED", None, f"no invoice {ev.invoice_id} for customer", None
    seen = await invoicing_service.attempt_statuses(conn, ev.object_id)
    amount = from_minor_units(ev.amount_minor, ev.currency)
    if kind == hook.PAYMENT_SUCCEEDED:
        if "CAPTURED" in seen:
            return "IGNORED", tenant, "already recorded", ev.invoice_id
        done = await invoicing_service.record_reported_payment(
            conn,
            ev.invoice_id,
            status="CAPTURED",
            provider_ref=ev.object_id,
            failure_code=None,
            amount=amount,
            idempotency_key=f"stripe-{ev.event_id}",
            now=now,
        )
        if done is not None and done.get("replayed"):
            return "IGNORED", tenant, "already recorded", ev.invoice_id
        if done is None:
            return (
                "NEEDS_REVIEW",
                tenant,
                f"payment {ev.object_id} for an invoice not collectable",
                ev.invoice_id,
            )
        return "APPLIED", tenant, ev.object_id, ev.invoice_id
    if seen:
        return "IGNORED", tenant, "already recorded", ev.invoice_id
    err = ev.data.get("last_payment_error") or {}
    code = str(err.get("decline_code") or err.get("code") or "PAYMENT_FAILED").upper()[:60]
    done = await invoicing_service.record_reported_payment(
        conn,
        ev.invoice_id,
        status="FAILED",
        provider_ref=ev.object_id,
        failure_code=code,
        amount=amount,
        idempotency_key=f"stripe-{ev.event_id}",
        now=now,
    )
    return ("APPLIED" if done else "IGNORED"), tenant, ev.object_id, ev.invoice_id


async def process(engine: AsyncEngine, ev: hook.StripeEvent, *, now: datetime | None = None) -> str:
    """Apply one verified event; returns its outcome, or DUPLICATE when this event id was already handled."""
    now = now or datetime.now(UTC)
    async with engine.begin() as conn:
        await conn.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:e, 0))"), {"e": ev.event_id}
        )
        if (
            await conn.execute(
                text("SELECT 1 FROM commercial.stripe_event WHERE event_id = :e"),
                {"e": ev.event_id},
            )
        ).first():
            return "DUPLICATE"
        outcome, tenant, detail, invoice = await _apply(conn, ev, now)
        await _in_tenant(conn, "")
        amount = (
            from_minor_units(ev.amount_minor, ev.currency)
            if ev.amount_minor is not None and ev.currency
            else None
        )
        await conn.execute(
            text(
                "INSERT INTO commercial.stripe_event (event_id, event_type, customer_ref, tenant_id, outcome, "
                "detail, received_at, invoice_id, amount, currency) "
                "VALUES (:e, :k, :c, :t, :o, :d, :n, :i, :m, :u)"
            ),
            {
                "e": ev.event_id,
                "k": ev.event_type[:120],
                "c": ev.customer_ref,
                "t": uuid.UUID(tenant) if tenant else None,
                "o": outcome,
                "d": detail,
                "n": now,
                "i": uuid.UUID(invoice) if invoice else None,
                "m": amount,
                "u": ev.currency,
            },
        )
        return outcome


async def review_queue(
    conn: AsyncConnection, *, include_resolved: bool = False
) -> list[dict[str, object]]:
    """Events a person has to look at, oldest first: refunds, disputes, payments for invoices that cannot take them,
    and payments that name no invoice of the customer."""
    rows = (
        (
            await conn.execute(
                text(
                    "SELECT event_id, event_type, tenant_id, outcome, detail, received_at, invoice_id, amount, "
                    "currency, resolved_at, resolution FROM commercial.stripe_event "
                    "WHERE outcome IN ('NEEDS_REVIEW', 'UNMATCHED') "
                    + ("" if include_resolved else "AND resolved_at IS NULL ")
                    + "ORDER BY received_at LIMIT 200"
                )
            )
        )
        .mappings()
        .all()
    )
    return [
        {
            "event_id": r["event_id"],
            "event_type": r["event_type"],
            "outcome": r["outcome"],
            "detail": r["detail"],
            "received_at": r["received_at"].isoformat(),
            "tenant_id": str(r["tenant_id"]) if r["tenant_id"] else None,
            "invoice_id": str(r["invoice_id"]) if r["invoice_id"] else None,
            "amount": str(Decimal(r["amount"]).quantize(Decimal("0.01")))
            if r["amount"] is not None
            else None,
            "currency": r["currency"],
            "resolved": r["resolved_at"] is not None,
            "resolution": r["resolution"],
        }
        for r in rows
    ]


async def open_refund(conn: AsyncConnection, event_id: str) -> dict[str, object] | None:
    """An open refund event that names a company's own invoice, with what a credit note needs; None otherwise."""
    row = (
        (
            await conn.execute(
                text(
                    "SELECT event_id, tenant_id, invoice_id, amount, currency FROM commercial.stripe_event "
                    "WHERE event_id = :e AND event_type = 'charge.refunded' AND outcome = 'NEEDS_REVIEW' "
                    "AND resolved_at IS NULL AND tenant_id IS NOT NULL AND invoice_id IS NOT NULL "
                    "AND amount IS NOT NULL"
                ),
                {"e": event_id},
            )
        )
        .mappings()
        .first()
    )
    return dict(row) if row else None


async def resolve_event(
    conn: AsyncConnection,
    event_id: str,
    *,
    operator: str,
    resolution: str,
    now: datetime | None = None,
) -> bool:
    """Close one open review item with the operator's note. False when there is no such open item."""
    res = await conn.execute(
        text(
            "UPDATE commercial.stripe_event SET resolved_by = :o, resolved_at = :n, resolution = :r "
            "WHERE event_id = :e AND outcome IN ('NEEDS_REVIEW', 'UNMATCHED') AND resolved_at IS NULL"
        ),
        {"o": uuid.UUID(operator), "n": now or datetime.now(UTC), "r": resolution, "e": event_id},
    )
    return res.rowcount == 1
