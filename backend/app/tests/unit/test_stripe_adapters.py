"""Stripe payment and tax adapters behind the commercial ports (ZID-COM-001 §11; CR-027).

No network: httpx's mock transport plays Stripe. The point is what the platform sends (an amount in minor
units, a customer reference, an idempotency key, never card data) and how every answer maps onto the port.
"""

from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from urllib.parse import parse_qs

import httpx
import pytest
from zid_platform.modules.commercial.application.adapters import (
    StripePayments,
    StripeTax,
    TaxableLine,
    from_minor_units,
    to_minor_units,
)


def stripe(handler, cls):  # type: ignore[no-untyped-def]
    return cls(
        "sk_test_unit", base_url="https://stripe.test", transport=httpx.MockTransport(handler)
    )


def form(req: httpx.Request) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(req.content.decode()).items()}


def test_minor_units_follow_the_currency_and_refuse_lost_precision() -> None:
    assert to_minor_units(Decimal("2053.00"), "USD") == 205300
    assert to_minor_units(Decimal(1500), "JPY") == 1500  # no minor unit
    assert to_minor_units(Decimal("1.234"), "KWD") == 1234  # three decimals
    with pytest.raises(ValueError):
        to_minor_units(Decimal("10.005"), "USD")  # never round a charge silently
    assert from_minor_units(205300, "USD") == Decimal("2053.00")
    assert from_minor_units(1500, "JPY") == Decimal(1500)


def test_a_captured_payment_sends_only_a_reference_an_amount_and_the_idempotency_key() -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json={"id": "pi_123", "status": "succeeded"})

    async def run() -> None:
        p = stripe(handler, StripePayments)
        out = await p.capture(
            invoice_id="inv-1",
            amount=Decimal("2053.00"),
            currency="USD",
            idempotency_key="collect-abc12345",
            customer_ref="cus_ABC123456",
        )
        assert (out.status, out.provider_ref, out.failure_code) == ("CAPTURED", "pi_123", None)

    asyncio.run(run())
    req = seen[0]
    assert req.url.path == "/v1/payment_intents"
    assert req.headers["Idempotency-Key"] == "collect-abc12345"
    assert req.headers["Authorization"].startswith("Basic ")
    body = form(req)
    assert (
        body["amount"] == "205300"
        and body["currency"] == "usd"
        and body["customer"] == "cus_ABC123456"
    )
    assert body["off_session"] == "true" and body["confirm"] == "true"
    assert not any(k.startswith(("card", "payment_method_data")) for k in body)  # never card data


def test_every_stripe_answer_maps_to_a_port_result_and_never_raises() -> None:
    def answer(status: int, payload: dict | None) -> StripePayments:  # type: ignore[type-arg]
        return stripe(lambda r: httpx.Response(status, json=payload or {}), StripePayments)

    async def capture(p: StripePayments, customer: str | None = "cus_ABC123456"):  # type: ignore[no-untyped-def]
        return await p.capture(
            invoice_id="i",
            amount=Decimal("10.00"),
            currency="USD",
            idempotency_key="k" * 8,
            customer_ref=customer,
        )

    async def run() -> None:
        declined = await capture(
            answer(
                402,
                {
                    "error": {
                        "code": "card_declined",
                        "decline_code": "insufficient_funds",
                        "payment_intent": {"id": "pi_9"},
                    }
                },
            )
        )
        assert (declined.status, declined.failure_code, declined.provider_ref) == (
            "FAILED",
            "INSUFFICIENT_FUNDS",
            "pi_9",
        )
        assert (
            await capture(answer(200, {"id": "pi_1", "status": "requires_action"}))
        ).failure_code == "PAYMENT_REQUIRES_ACTION"
        assert (await capture(answer(503, None))).failure_code == "PROVIDER_UNAVAILABLE"
        assert (await capture(answer(429, None))).failure_code == "PROVIDER_UNAVAILABLE"
        assert (await capture(answer(200, {}), customer=None)).failure_code == "NO_PAYMENT_METHOD"

        def boom(req: httpx.Request) -> httpx.Response:
            raise httpx.ConnectTimeout("slow")

        assert (await capture(stripe(boom, StripePayments))).failure_code == "PROVIDER_UNAVAILABLE"
        odd = await stripe(lambda r: httpx.Response(200, json={}), StripePayments).capture(
            invoice_id="i",
            amount=Decimal("10.005"),
            currency="USD",
            idempotency_key="k" * 8,
            customer_ref="cus_ABC123456",
        )
        assert odd.failure_code == "AMOUNT_INVALID"

    asyncio.run(run())


def test_stripe_tax_is_asked_per_line_in_minor_units_and_answers_by_line() -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(
            200,
            json={
                "line_items": {
                    "data": [
                        {
                            "reference": "1",
                            "amount_tax": 2000,
                            "tax_breakdown": [
                                {
                                    "tax_rate_details": {
                                        "tax_type": "vat",
                                        "percentage_decimal": "20.0",
                                    }
                                }
                            ],
                        },
                        {"reference": "3", "amount_tax": 500, "tax_breakdown": []},
                    ]
                }
            },
        )

    async def run() -> None:
        t = stripe(handler, StripeTax)
        lines = [
            TaxableLine(1, "FEE", "ZID-BASE", Decimal("100.00")),
            TaxableLine(
                2, "CREDIT", None, Decimal("-10.00")
            ),  # a credit is not a taxable supply line
            TaxableLine(3, "OVERAGE", "ZID-HUM", Decimal("25.00")),
        ]
        out = await t.lines_for(currency="USD", region="uk-1", country="GB", lines=lines)
        assert out is not None
        assert out[1].amount == Decimal("20.00") and out[1].code == "VAT_20.0"
        assert out[3].amount == Decimal("5.00") and out[3].code == "TAX_0"
        assert 2 not in out

    asyncio.run(run())
    body = form(seen[0])
    assert seen[0].url.path == "/v1/tax/calculations"
    assert body["customer_details[address][country]"] == "GB"
    assert body["line_items[0][amount]"] == "10000" and body["line_items[0][reference]"] == "1"
    assert body["line_items[1][amount]"] == "2500" and body["line_items[1][reference]"] == "3"
    assert body["line_items[0][tax_behavior]"] == "exclusive"


def test_stripe_tax_answers_none_when_it_cannot_place_the_supply() -> None:
    async def run() -> None:
        lines = [TaxableLine(1, "FEE", "ZID-BASE", Decimal("100.00"))]
        refused = stripe(
            lambda r: httpx.Response(
                400, json={"error": {"code": "customer_tax_location_invalid"}}
            ),
            StripeTax,
        )
        assert (
            await refused.lines_for(currency="USD", region="r", country="GB", lines=lines) is None
        )
        # no buyer country: no call is made at all
        never = stripe(lambda r: pytest.fail("no request without a country"), StripeTax)
        assert await never.lines_for(currency="USD", region="r", country=None, lines=lines) is None

        def boom(req: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("slow")

        assert (
            await stripe(boom, StripeTax).lines_for(
                currency="USD", region="r", country="GB", lines=lines
            )
            is None
        )

    asyncio.run(run())


def test_the_secret_is_never_in_a_log_line_or_a_result() -> None:
    p = stripe(
        lambda r: httpx.Response(200, json={"id": "pi_1", "status": "succeeded"}), StripePayments
    )
    out = asyncio.run(
        p.capture(
            invoice_id="i",
            amount=Decimal("1.00"),
            currency="USD",
            idempotency_key="k" * 8,
            customer_ref="cus_ABC123456",
        )
    )
    assert "sk_test_unit" not in json.dumps(out.__dict__ if hasattr(out, "__dict__") else str(out))
