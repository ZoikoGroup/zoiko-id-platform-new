"""Paying one invoice on Stripe's page, with the bank's confirmation step (CR-041). No network: a mock Stripe."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest
from zid_platform.modules.commercial.application.adapters import PaymentSetupError, StripeCustomers

CUSTOMER = "cus_ABC123456"
SESSION = "cs_test_pay12345"


def stripe(handler: Any) -> StripeCustomers:
    return StripeCustomers(
        "sk_test_unit", base_url="https://stripe.test", transport=httpx.MockTransport(handler)
    )


def paid(**over: Any) -> dict[str, Any]:
    return {
        "mode": "payment",
        "customer": CUSTOMER,
        "payment_status": "paid",
        "payment_intent": "pi_1",
        "amount_total": 4200,
        "currency": "usd",
        "metadata": {"invoice_id": "inv-1"},
    } | over


def test_the_payment_page_carries_the_invoice_and_amount_and_never_a_card() -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json={"id": SESSION, "url": "https://checkout.stripe.test/pay"})

    url = asyncio.run(
        stripe(handler).start_payment(
            customer_ref=CUSTOMER,
            invoice_id="inv-1",
            invoice_label="Invoice ZID-00000007",
            amount=Decimal("42.00"),
            currency="USD",
            success_url="https://c.test/ok?session_id={CHECKOUT_SESSION_ID}",
            cancel_url="https://c.test/no",
        )
    )
    form = {k: v[0] for k, v in parse_qs(seen[0].content.decode()).items()}
    assert url == "https://checkout.stripe.test/pay"
    assert form["mode"] == "payment" and form["customer"] == CUSTOMER
    assert form["line_items[0][price_data][unit_amount]"] == "4200"
    assert form["payment_intent_data[metadata][invoice_id]"] == "inv-1"
    assert not any("card[" in k or "number" in k for k in form)


def test_a_paid_session_of_this_customer_is_accepted() -> None:
    p = asyncio.run(
        stripe(lambda r: httpx.Response(200, json=paid())).complete_payment(
            session_id=SESSION, customer_ref=CUSTOMER
        )
    )
    assert (p.payment_intent, p.invoice_id, p.amount_minor, p.currency) == (
        "pi_1",
        "inv-1",
        4200,
        "USD",
    )


@pytest.mark.parametrize(
    "over",
    [
        {"customer": "cus_SOMEONEELSE"},
        {"payment_status": "unpaid"},
        {"mode": "setup"},
        {"payment_intent": None},
        {"metadata": {}},
    ],
)
def test_a_session_that_is_not_this_customers_or_not_paid_is_refused(over: dict[str, Any]) -> None:
    with pytest.raises(PaymentSetupError) as e:
        asyncio.run(
            stripe(lambda r: httpx.Response(200, json=paid(**over))).complete_payment(
                session_id=SESSION, customer_ref=CUSTOMER
            )
        )
    assert e.value.code == "PAYMENT_NOT_COMPLETE"


def test_a_value_that_is_not_a_session_is_refused_before_any_call() -> None:
    def never(req: httpx.Request) -> httpx.Response:
        raise AssertionError("no call expected")

    with pytest.raises(PaymentSetupError) as e:
        asyncio.run(stripe(never).complete_payment(session_id="pi_123", customer_ref=CUSTOMER))
    assert e.value.code == "SESSION_INVALID"
