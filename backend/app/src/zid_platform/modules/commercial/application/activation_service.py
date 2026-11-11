"""Pay before the plan switches (CR-062).

An activated order whose plan has a platform fee waits in AWAITING_PAYMENT until the company has paid the first month's fee on Stripe. The plan changes only
when that payment is confirmed. The amount is the same monthly value the commitment approval used, so the company approved and pays one figure. A paid
activation is shown as a CREDIT line on the month-end invoice, so the fee is not billed twice.

The browser coming back from Stripe never counts as payment: the session is read back from Stripe and must name this order, this company's customer and a
paid state before anything moves.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection
from zid_db import in_tenant

from zid_platform.modules.commercial.application import commitment_service as commitments
from zid_platform.modules.commercial.application.change_log import log_change
from zid_platform.modules.commercial.infrastructure.plan_request_store import operator_scope

AWAITING, PAID, CREDITED, CANCELLED, REFUND_DUE, REFUNDED = (
    "AWAITING",
    "PAID",
    "CREDITED",
    "CANCELLED",
    "REFUND_DUE",
    "REFUNDED",
)


def _money(v: Any) -> str:
    return format(Decimal(v).quantize(Decimal("0.01")), "f")


class ActivationError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail or code.replace("_", " ").capitalize() + "."


def _view(r: Any, plan: str | None = None, number: int | None = None) -> dict[str, Any]:
    return {
        "order_id": str(r["order_id"]),
        "plan": plan,
        "status": r["status"],
        "amount": _money(r["amount"]),
        "currency": r["currency"],
        "created_at": r["created_at"].isoformat() if r["created_at"] else None,
        "paid_at": r["paid_at"].isoformat() if r["paid_at"] else None,
    }


async def load(conn: AsyncConnection, tenant_id: str, order_id: str) -> Any | None:
    async with in_tenant(conn, tenant_id):
        return (
            (
                await conn.execute(
                    text(
                        "SELECT * FROM commercial.activation_payment WHERE order_id = :o FOR UPDATE"
                    ),
                    {"o": uuid.UUID(order_id)},
                )
            )
            .mappings()
            .first()
        )


async def hold(
    conn: AsyncConnection, o: Any, *, actor: str, now: datetime
) -> dict[str, Any] | None:
    """Put a signed order that has something to pay into AWAITING_PAYMENT. None means "nothing to wait for": it is already paid, or the monthly value is nil."""
    tenant, order_id = str(o["tenant_id"]), str(o["order_id"])
    row = await load(conn, tenant, order_id)
    if row is not None and row["status"] in (PAID, CREDITED, REFUND_DUE, REFUNDED):
        return None
    minor = await commitments.monthly_value_minor(conn, o["quote_id"])
    if minor <= 0:
        return None
    currency = (
        await conn.execute(
            text("SELECT currency FROM commercial.quote WHERE quote_id = :q"), {"q": o["quote_id"]}
        )
    ).scalar_one()
    amount = (Decimal(minor) / 100).quantize(Decimal("0.01"))
    async with in_tenant(conn, tenant):
        if row is None:
            await conn.execute(
                text(
                    "INSERT INTO commercial.activation_payment (order_id, tenant_id, currency, amount, activated_by, status) "
                    "VALUES (:o, :t, :c, :a, :by, 'AWAITING')"
                ),
                {
                    "o": uuid.UUID(order_id),
                    "t": uuid.UUID(tenant),
                    "c": currency,
                    "a": amount,
                    "by": uuid.UUID(actor),
                },
            )
        else:  # asked again after a cancellation: the amount is re-read, the session is forgotten
            await conn.execute(
                text(
                    "UPDATE commercial.activation_payment SET status = 'AWAITING', amount = :a, currency = :c, activated_by = :by, session_id = NULL "
                    "WHERE order_id = :o"
                ),
                {"o": uuid.UUID(order_id), "a": amount, "c": currency, "by": uuid.UUID(actor)},
            )
    await conn.execute(
        text("UPDATE commercial.sales_order SET status = 'AWAITING_PAYMENT' WHERE order_id = :o"),
        {"o": uuid.UUID(order_id)},
    )
    await log_change(
        conn,
        kind="ORDER_AWAITING_PAYMENT",
        ref=order_id,
        tenant_id=tenant,
        actor=actor,
        detail={"plan": o["plan_code"], "amount": format(amount, "f"), "currency": currency},
    )
    return {
        "awaiting_payment": True,
        "order_id": order_id,
        "plan": o["plan_code"],
        "amount": format(amount, "f"),
        "currency": currency,
    }


async def cancel(
    conn: AsyncConnection, tenant_id: str, order_id: str, *, actor: str
) -> dict[str, Any]:
    """The operator withdraws the wait: the order goes back to SIGNED and nothing was charged. A paid one cannot be cancelled here (that is a refund)."""
    row = await load(conn, tenant_id, order_id)
    if row is None or row["status"] != AWAITING:
        raise ActivationError(
            "ACTIVATION_NOT_WAITING",
            "Only an order that is still waiting for payment can be taken back.",
        )
    async with in_tenant(conn, tenant_id):
        await conn.execute(
            text(
                "UPDATE commercial.activation_payment SET status = 'CANCELLED' WHERE order_id = :o"
            ),
            {"o": uuid.UUID(order_id)},
        )
    await conn.execute(
        text(
            "UPDATE commercial.sales_order SET status = 'SIGNED' WHERE order_id = :o AND status = 'AWAITING_PAYMENT'"
        ),
        {"o": uuid.UUID(order_id)},
    )
    await log_change(
        conn, kind="ORDER_PAYMENT_WAIT_CANCELLED", ref=order_id, tenant_id=tenant_id, actor=actor
    )
    return {"order_id": order_id, "status": CANCELLED}


async def waiting(conn: AsyncConnection) -> list[dict[str, Any]]:
    """The caller's own orders that wait for payment, newest first (the tenant is the transaction's)."""
    rows = (
        await conn.execute(
            text(
                "SELECT p.*, o.plan_code FROM commercial.activation_payment p JOIN commercial.sales_order o ON o.order_id = p.order_id "
                "WHERE p.status = 'AWAITING' ORDER BY p.created_at DESC"
            )
        )
    ).mappings()
    return [{**_view(r), "plan": r["plan_code"]} for r in rows]


async def waiting_one(conn: AsyncConnection, tenant_id: str, order_id: str) -> Any:
    row = await load(conn, tenant_id, order_id)
    if row is None or row["status"] != AWAITING:
        raise ActivationError("ACTIVATION_NOT_WAITING", "That order is not waiting for payment.")
    return row


async def remember_session(
    conn: AsyncConnection, tenant_id: str, order_id: str, session_id: str
) -> None:
    async with in_tenant(conn, tenant_id):
        await conn.execute(
            text("UPDATE commercial.activation_payment SET session_id = :s WHERE order_id = :o"),
            {"s": session_id, "o": uuid.UUID(order_id)},
        )


async def record_payment(
    conn: AsyncConnection,
    tenant_id: str,
    order_id: str,
    *,
    payment_ref: str,
    amount_minor: int,
    currency: str,
) -> dict[str, Any]:
    """Mark the wait PAID once Stripe says this order was paid in full. Idempotent: the same payment again changes nothing; a different amount or currency is refused."""
    row = await load(conn, tenant_id, order_id)
    if row is None:
        raise ActivationError("ACTIVATION_NOT_FOUND", "No payment was asked for on that order.")
    if row["status"] in (PAID, CREDITED):
        return {**_view(row), "already_recorded": True}
    if row["status"] != AWAITING:
        raise ActivationError("ACTIVATION_NOT_WAITING", "That order is not waiting for payment.")
    if currency.upper() != row["currency"] or Decimal(amount_minor) / 100 != Decimal(row["amount"]):
        raise ActivationError(
            "ACTIVATION_AMOUNT_MISMATCH",
            "The amount paid is not the amount asked for, so nothing was recorded.",
        )
    async with in_tenant(conn, tenant_id):
        await conn.execute(
            text(
                "UPDATE commercial.activation_payment SET status = 'PAID', payment_ref = :r, paid_at = :n WHERE order_id = :o"
            ),
            {"r": payment_ref, "n": datetime.now(UTC), "o": uuid.UUID(order_id)},
        )
    await log_change(
        conn,
        kind="ORDER_ACTIVATION_PAID",
        ref=order_id,
        tenant_id=tenant_id,
        actor=str(row["activated_by"]),
        detail={"amount": _money(row["amount"]), "currency": row["currency"]},
    )
    return {**_view(row), "status": PAID, "already_recorded": False}


async def unapplied_credits(conn: AsyncConnection, tenant_id: str, currency: str) -> list[Any]:
    """Paid activations that still have some of their amount to take off an invoice, in this currency (the transaction's tenant). `balance` is what is left."""
    async with in_tenant(conn, tenant_id):
        return list(
            (
                await conn.execute(
                    text(
                        "SELECT p.order_id, p.amount, p.amount - coalesce((SELECT sum(c.amount) FROM commercial.activation_credit c WHERE c.order_id = p.order_id AND c.released_at IS NULL), 0) AS balance "
                        "FROM commercial.activation_payment p WHERE p.tenant_id = :t AND p.currency = :c AND p.status = 'PAID' "
                        "AND p.amount > coalesce((SELECT sum(c.amount) FROM commercial.activation_credit c WHERE c.order_id = p.order_id AND c.released_at IS NULL), 0) "
                        "ORDER BY p.paid_at FOR UPDATE OF p"
                    ),
                    {"t": uuid.UUID(tenant_id), "c": currency},
                )
            ).mappings()
        )


async def apply_credit(
    conn: AsyncConnection, tenant_id: str, order_id: Any, invoice_id: Any, amount: Decimal
) -> None:
    """Record that this invoice took `amount` off a paid activation; when the whole payment is used the activation is CREDITED."""
    async with in_tenant(conn, tenant_id):
        await conn.execute(
            text(
                "INSERT INTO commercial.activation_credit (order_id, invoice_id, tenant_id, amount) VALUES (:o, :i, :t, :a)"
            ),
            {"o": order_id, "i": invoice_id, "t": uuid.UUID(tenant_id), "a": amount},
        )
        await conn.execute(
            text(
                "UPDATE commercial.activation_payment p SET status = 'CREDITED', credited_invoice_id = :i WHERE p.order_id = :o "
                "AND p.amount <= (SELECT sum(c.amount) FROM commercial.activation_credit c WHERE c.order_id = p.order_id AND c.released_at IS NULL)"
            ),
            {"o": order_id, "i": invoice_id},
        )


async def release_credits(conn: AsyncConnection, tenant_id: str, invoice_id: str) -> int:
    """A draft invoice was voided: what it took off paid activations is given back, so the next invoice can take it. Returns how many were released."""
    async with in_tenant(conn, tenant_id):
        released = (
            await conn.execute(
                text(
                    "UPDATE commercial.activation_credit SET released_at = now() WHERE invoice_id = :i AND released_at IS NULL RETURNING order_id"
                ),
                {"i": uuid.UUID(invoice_id)},
            )
        ).all()
        for (order_id,) in released:
            await conn.execute(
                text(
                    "UPDATE commercial.activation_payment SET status = 'PAID', credited_invoice_id = NULL WHERE order_id = :o AND status = 'CREDITED'"
                ),
                {"o": order_id},
            )
    return len(released)


async def request_refund(
    conn: AsyncConnection, tenant_id: str, order_id: str, *, actor: str, reason: str
) -> dict[str, Any]:
    """Operator plane: the payment is to be returned. Only a payment that no invoice has taken anything off can be returned this way (otherwise the invoice is corrected
    with a credit note). The refund itself is made at the processor; when it reports it, the payment becomes REFUNDED."""
    reason = reason.strip()
    if len(reason) < 3 or len(reason) > 500:
        raise ActivationError("VALIDATION_FAILED", "Give a reason of 3 to 500 characters.")
    row = await load(conn, tenant_id, order_id)
    if row is None:
        raise ActivationError("ACTIVATION_NOT_FOUND", "No payment was asked for on that order.")
    if row["status"] == REFUND_DUE:
        return {"order_id": order_id, "status": REFUND_DUE, "already_requested": True}
    if row["status"] != PAID:
        raise ActivationError(
            "ACTIVATION_REFUND_NOT_ALLOWED",
            "Only a paid activation that no invoice has used can be refunded here; an invoice that used it is corrected with a credit note.",
        )
    async with in_tenant(conn, tenant_id):
        await conn.execute(
            text(
                "UPDATE commercial.activation_payment SET status = 'REFUND_DUE', refund_reason = :r, refund_decided_by = :by, refund_decided_at = :n WHERE order_id = :o"
            ),
            {"r": reason, "by": uuid.UUID(actor), "n": datetime.now(UTC), "o": uuid.UUID(order_id)},
        )
    await log_change(
        conn,
        kind="ORDER_ACTIVATION_REFUND_DUE",
        ref=order_id,
        tenant_id=tenant_id,
        actor=actor,
        reason=reason,
    )
    return {
        "order_id": order_id,
        "status": REFUND_DUE,
        "already_requested": False,
        "payment_ref": row["payment_ref"],
        "amount": _money(row["amount"]),
        "currency": row["currency"],
    }


async def refunds_due(conn: AsyncConnection) -> list[dict[str, Any]]:
    """Operator plane: every company's activation payments waiting to be returned, oldest first, with the processor reference to refund."""
    async with operator_scope(conn):
        rows = (
            await conn.execute(
                text(
                    "SELECT * FROM commercial.activation_payment WHERE status = 'REFUND_DUE' ORDER BY refund_decided_at"
                )
            )
        ).mappings()
        return [
            {
                "order_id": str(r["order_id"]),
                "tenant_id": str(r["tenant_id"]),
                "amount": _money(r["amount"]),
                "currency": r["currency"],
                "payment_ref": r["payment_ref"],
                "reason": r["refund_reason"],
                "decided_at": r["refund_decided_at"].isoformat()
                if r["refund_decided_at"]
                else None,
            }
            for r in rows
        ]


async def note_refunded(conn: AsyncConnection, payment_intent: str, *, now: datetime) -> str | None:
    """The processor reported a refund of this payment: the activation payment becomes REFUNDED and is never credited to an invoice. Returns the order, or None
    when the payment was not an activation payment."""
    async with operator_scope(conn):
        got = (
            await conn.execute(
                text(
                    "UPDATE commercial.activation_payment SET status = 'REFUNDED', refunded_at = :n WHERE payment_ref = :r AND status IN ('PAID', 'REFUND_DUE') RETURNING order_id"
                ),
                {"r": payment_intent, "n": now},
            )
        ).scalar()
    return str(got) if got else None
