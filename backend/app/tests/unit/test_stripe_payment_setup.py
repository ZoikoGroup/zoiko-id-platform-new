"""Saving a company's card on Stripe's hosted page (CR-034). No network: httpx's mock transport plays Stripe.

The point is what the platform sends (a customer, a setup-mode Checkout session with return addresses, never card
data) and what it refuses to trust (a returned session that is not this customer's, not finished, or not a session)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest
from zid_platform.modules.commercial.application.adapters import PaymentSetupError, StripeCustomers

CUSTOMER = "cus_ABC123456"
SESSION = "cs_test_a1b2c3d4e5"


def stripe(handler: Callable[[httpx.Request], httpx.Response]) -> StripeCustomers:
    return StripeCustomers(
        "sk_test_unit", base_url="https://stripe.test", transport=httpx.MockTransport(handler)
    )


def form(req: httpx.Request) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(req.content.decode()).items()}


def card(brand: str = "visa", last4: str = "4242") -> dict[str, Any]:
    return {
        "id": "pm_1",
        "type": "card",
        "card": {"brand": brand, "last4": last4, "exp_month": 8, "exp_year": 2030},
    }


def test_a_customer_and_a_setup_session_carry_references_and_return_addresses_only() -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        if req.url.path == "/v1/customers":
            return httpx.Response(200, json={"id": CUSTOMER})
        return httpx.Response(
            200, json={"id": SESSION, "url": "https://checkout.stripe.test/c/pay/" + SESSION}
        )

    async def run() -> None:
        s = stripe(handler)
        assert (
            await s.create_customer(
                name="Acme Ltd", tenant_id="t-1", idempotency_key="customer-acc-1"
            )
            == CUSTOMER
        )
        url = await s.start_setup(
            customer_ref=CUSTOMER,
            success_url="https://console.test/admin/billing?payment=saved&session_id={CHECKOUT_SESSION_ID}",
            cancel_url="https://console.test/admin/billing?payment=cancelled",
        )
        assert url.startswith("https://checkout.stripe.test/")

    asyncio.run(run())
    customer_form, session_form = form(seen[0]), form(seen[1])
    assert seen[0].headers["Idempotency-Key"] == "customer-acc-1"
    assert customer_form == {"name": "Acme Ltd", "metadata[tenant_id]": "t-1"}
    assert session_form["mode"] == "setup" and session_form["customer"] == CUSTOMER
    assert session_form["success_url"].endswith("session_id={CHECKOUT_SESSION_ID}")
    assert not any(
        "card[" in k or "number" in k for k in {*customer_form, *session_form}
    )  # never card data


def test_a_finished_session_of_this_customer_makes_its_card_the_default() -> None:
    posted: list[dict[str, str]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == f"/v1/checkout/sessions/{SESSION}":
            return httpx.Response(
                200,
                json={
                    "customer": CUSTOMER,
                    "status": "complete",
                    "setup_intent": {"status": "succeeded", "payment_method": "pm_1"},
                },
            )
        if req.url.path == f"/v1/customers/{CUSTOMER}":
            posted.append(form(req))
            return httpx.Response(200, json={"id": CUSTOMER})
        return httpx.Response(200, json=card())

    out = asyncio.run(stripe(handler).complete_setup(session_id=SESSION, customer_ref=CUSTOMER))
    assert (out.brand, out.last4, out.exp_month, out.exp_year) == ("visa", "4242", 8, 2030)
    assert posted == [{"invoice_settings[default_payment_method]": "pm_1"}]


@pytest.mark.parametrize(
    "session",
    [
        {
            "customer": "cus_OTHER99999",
            "status": "complete",
            "setup_intent": {"status": "succeeded", "payment_method": "pm_1"},
        },
        {
            "customer": CUSTOMER,
            "status": "open",
            "setup_intent": {"status": "requires_payment_method"},
        },
        {
            "customer": CUSTOMER,
            "status": "complete",
            "setup_intent": {"status": "canceled", "payment_method": "pm_1"},
        },
        {"customer": CUSTOMER, "status": "complete", "setup_intent": None},
    ],
)
def test_a_session_that_is_not_this_customers_or_not_finished_saves_nothing(
    session: dict[str, Any],
) -> None:
    posted: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "POST":
            posted.append(req)
        return httpx.Response(200, json=session)

    with pytest.raises(PaymentSetupError) as raised:
        asyncio.run(stripe(handler).complete_setup(session_id=SESSION, customer_ref=CUSTOMER))
    assert raised.value.code == "SETUP_NOT_COMPLETE" and posted == []


def test_a_value_that_is_not_a_session_id_never_reaches_stripe() -> None:
    def handler(req: httpx.Request) -> httpx.Response:  # pragma: no cover - must not be called
        raise AssertionError("no request expected")

    for bad in ("pi_123", "../customers", "x" * 300):
        with pytest.raises(PaymentSetupError, match="SESSION_INVALID"):
            asyncio.run(stripe(handler).complete_setup(session_id=bad, customer_ref=CUSTOMER))


def test_the_current_card_is_brand_and_last_four_or_nothing() -> None:
    def with_default(default: str | None) -> StripeCustomers:
        def handler(req: httpx.Request) -> httpx.Response:
            if req.url.path == f"/v1/customers/{CUSTOMER}":
                return httpx.Response(
                    200, json={"invoice_settings": {"default_payment_method": default}}
                )
            return httpx.Response(200, json=card("mastercard", "4444"))

        return stripe(handler)

    assert asyncio.run(with_default(None).current_method(CUSTOMER)) is None
    out = asyncio.run(with_default("pm_1").current_method(CUSTOMER))
    assert out is not None and (out.brand, out.last4) == ("mastercard", "4444")


def test_a_processor_outage_or_rejection_is_a_coded_error_not_a_stack() -> None:
    def down(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    for handler, code in (
        (down, "PROVIDER_UNAVAILABLE"),
        (lambda r: httpx.Response(429, json={}), "PROVIDER_UNAVAILABLE"),
        (
            lambda r: httpx.Response(400, json={"error": {"code": "resource_missing"}}),
            "RESOURCE_MISSING",
        ),
    ):
        with pytest.raises(PaymentSetupError) as raised:
            asyncio.run(stripe(handler).current_method(CUSTOMER))
        assert raised.value.code == code


def test_changing_the_company_card_is_owner_only_and_catalogued() -> None:
    """CR-034: the owner holds `commercial.payment.manage`; the set handed down to administrators leaves it out."""
    from zid_platform.modules.onboarding.domain.gates import (
        OWNER_ONLY_ACTIONS,
        TENANT_ADMIN_ACTIONS,
    )
    from zid_platform.protected import catalog

    assert "commercial.payment.manage" in OWNER_ONLY_ACTIONS
    assert OWNER_ONLY_ACTIONS <= set(TENANT_ADMIN_ACTIONS) and OWNER_ONLY_ACTIONS <= set(catalog())
    delegated = [a for a in TENANT_ADMIN_ACTIONS if a not in OWNER_ONLY_ACTIONS]
    assert "commercial.payment.manage" not in delegated and "commercial.plan.request" in delegated
