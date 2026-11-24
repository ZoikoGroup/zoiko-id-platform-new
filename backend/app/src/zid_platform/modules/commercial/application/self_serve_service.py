"""Self-serve plan purchase (CR-147): a company buys a published plan and the plan switches only after Stripe confirms the payment.

This is the missing front half of pay-before-switch (CR-062). It makes no new money rule: it raises an ordinary quote and signed order for the plan from the
APPROVED price book, at list price, and puts the order into AWAITING_PAYMENT through `orders_service.activate(require_payment=True)`. The existing activation
routes then take the payment on Stripe's page and switch the plan on a confirmed, exact payment. Nothing here charges a card or changes an entitlement.

Rules (ZID-UX-PRICE-004 WF-P08, ZID-COM-001):
- the price comes only from the approved price book, never from code: no approved book, no offer;
- only Launch, Business and Scale are self-serve, and only upwards: Developer is automatic, Enterprise is a contract, a downgrade takes effect at the end of a period;
- the buyer states the amount they reviewed; if the book moved since, the purchase is refused so they review again;
- a second press of the button reuses the order that is already waiting, so there is one order and one payment per purchase.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection
from zid_db import in_tenant

from zid_platform.modules.commercial.application import orders_service as orders
from zid_platform.modules.commercial.domain.orders import OrderError, QuoteLine
from zid_platform.modules.commercial.infrastructure import catalogue_store as store

SELF_SERVE_PLANS = ("LAUNCH", "BUSINESS", "SCALE")
BASE_SKU = "ZID-BASE"


class SelfServeError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail or code.replace("_", " ").capitalize() + "."


def _money(v: Decimal) -> str:
    return format(v.quantize(Decimal("0.01")), "f")


async def _current_plan(conn: AsyncConnection, tenant_id: str) -> str | None:
    async with in_tenant(conn, tenant_id):
        ent = await store.active_entitlement(conn)
    return ent["plan_code"] if ent else None


async def offer(
    conn: AsyncConnection, tenant_id: str, plan_code: str, *, now: datetime | None = None
) -> dict[str, Any]:
    """What it would cost to move to this plan today, or why it cannot be bought here. Reads only; the answer is the approved book's figure."""
    now = now or datetime.now(UTC)
    code = plan_code.strip().upper()
    if code not in SELF_SERVE_PLANS:
        raise SelfServeError(
            "PLAN_NOT_SELF_SERVE",
            "This plan is arranged with our team. Choose Launch, Business or Scale to buy here.",
        )
    book = await store.approved_price_book(conn, now)
    if book is None:
        raise SelfServeError(
            "NO_APPROVED_PRICE_BOOK",
            "Plan prices are not published yet, so a plan cannot be bought here. Ask our team for a quote.",
        )
    plan = await store.plan(conn, code)
    if plan is None or not plan.get("published"):
        raise SelfServeError("PLAN_NOT_FOUND", "That plan is not on offer.")
    line = next(
        (
            ln
            for ln in await store.lines(conn, str(book["price_book_id"]))
            if ln["sku_id"] == BASE_SKU and ln["plan_code"] == code and ln["released"]
        ),
        None,
    )
    fee = Decimal(line["unit_rate"]) if line else Decimal(0)
    if line is None or fee <= 0:
        raise SelfServeError("PRICE_NOT_PUBLISHED", "This plan has no published price yet.")
    current = await _current_plan(conn, tenant_id)
    if current is not None:
        if current == code:
            raise SelfServeError("ALREADY_ON_PLAN", "Your company is already on this plan.")
        now_plan = await store.plan(conn, current)
        if now_plan is not None and int(now_plan["sort_order"]) > int(plan["sort_order"]):
            raise SelfServeError(
                "DOWNGRADE_NOT_SELF_SERVE",
                "A move to a smaller plan takes effect at the end of your billing period. Ask our team to schedule it.",
            )
    return {
        "plan": code,
        "label": plan["label"],
        "amount": _money(fee),
        "currency": book["currency"],
        "price_book_id": str(book["price_book_id"]),
        "price_book_version": int(book["version"]),
        "fee_is_floor": bool(plan["fee_is_floor"]),
        "current_plan": current,
    }


async def _waiting_order(conn: AsyncConnection, tenant_id: str, plan: str) -> str | None:
    v = (
        await conn.execute(
            text(
                "SELECT order_id FROM commercial.sales_order WHERE tenant_id = :t AND plan_code = :p "
                "AND status = 'AWAITING_PAYMENT' ORDER BY created_at DESC LIMIT 1"
            ),
            {"t": uuid.UUID(tenant_id), "p": plan},
        )
    ).scalar()
    return str(v) if v else None


async def purchase(
    conn: AsyncConnection,
    tenant_id: str,
    plan_code: str,
    *,
    expected_amount: str,
    expected_currency: str,
    principal: str,
    legal_name: str | None,
    country: str | None,
    region: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Raise the order for this plan and hold it for payment. Returns the order and the exact amount to pay; the caller then opens Stripe for it."""
    now = now or datetime.now(UTC)
    o = await offer(conn, tenant_id, plan_code, now=now)
    try:
        asked = Decimal(expected_amount)
    except ArithmeticError:
        asked = Decimal(-1)
    if asked != Decimal(o["amount"]) or expected_currency.upper() != o["currency"]:
        raise SelfServeError(
            "PRICE_CHANGED",
            f"The price changed since you reviewed it. It is now {o['amount']} {o['currency']} a month. Review it again.",
        )
    waiting = await _waiting_order(conn, tenant_id, o["plan"])
    if waiting is not None:
        return {**o, "order_id": waiting, "reused": True}
    account = await orders.account_of(conn, tenant_id)
    if account is None:
        if not legal_name or not legal_name.strip():
            raise SelfServeError(
                "BILLING_DETAILS_REQUIRED",
                "Enter your company's legal name and country so the invoice is issued to the right company.",
            )
        account = await orders.create_account(
            conn,
            legal_name=legal_name.strip(),
            kind="DIRECT",
            country=(country or "").strip().upper() or None,
            currency=o["currency"],
            reseller_of=None,
            actor=principal,
        )
        await orders.link_tenant(conn, account, tenant_id, now)
    try:
        quote = await orders.create_quote(
            conn,
            account_id=account,
            price_book_id=o["price_book_id"],
            region=region,
            term_months=1,
            lines=[QuoteLine(1, BASE_SKU, o["plan"], Decimal(1))],
            prepaid=False,
            prepaid_discount=Decimal(0),
            liability_capped=True,
            residency_commitment=None,
            pilot=False,
            actor=principal,
            now=now,
        )
    except OrderError as e:
        raise SelfServeError(e.code, getattr(e, "detail", "") or "") from e
    if quote["status"] not in ("VALIDATED", "APPROVED"):
        raise SelfServeError(
            "PLAN_NOT_AVAILABLE",
            "This plan cannot be bought online for your company yet. Ask our team: "
            + ", ".join(quote.get("reasons") or [quote["status"]]).lower().replace("_", " ")
            + ".",
        )
    # what the buyer authorised, bound to the price they saw: the digest of the review
    digest = hashlib.sha256(
        f"{tenant_id}|{o['plan']}|{o['price_book_id']}|{o['amount']}|{o['currency']}|{principal}".encode()
    ).hexdigest()
    try:
        order = await orders.accept(
            conn,
            quote["quote_id"],
            tenant_id=tenant_id,
            signer_ref=principal,
            signed_document_digest=digest,
            effective_from=now,
            amends_order_id=None,
            actor=principal,
            now=now,
        )
        held = await orders.activate(
            conn, order["order_id"], actor=principal, now=now, direct=True, require_payment=True
        )
    except OrderError as e:
        raise SelfServeError(e.code, getattr(e, "detail", "") or "") from e
    if (
        held.get("status") != "AWAITING_PAYMENT"
    ):  # cannot happen for a positive fee, but if it ever does the plan must not be reported as bought
        raise SelfServeError(
            "PAYMENT_NOT_HELD", "The order could not be held for payment, so nothing was changed."
        )
    return {**o, "order_id": order["order_id"], "reused": False}
