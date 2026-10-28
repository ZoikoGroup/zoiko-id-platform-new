"""Stripe calls ride out a brief network or Stripe failure without ever repeating a write (CR-040)."""

from __future__ import annotations

import asyncio

import httpx
import pytest
from zid_platform.modules.commercial.application import adapters
from zid_platform.modules.commercial.application.adapters import PaymentSetupError, StripeCustomers


@pytest.fixture(autouse=True)
def _no_pauses(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adapters, "RETRY_PAUSES", (0.0, 0.0))


def customers(handler: object) -> StripeCustomers:
    return StripeCustomers(
        "sk_test_unit",
        base_url="https://stripe.test",
        transport=httpx.MockTransport(handler),  # type: ignore[arg-type]
    )


def test_a_dropped_connection_is_retried_and_the_write_keeps_one_idempotency_key() -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        if len(seen) < 3:
            raise httpx.ConnectTimeout("slow", request=req)
        return httpx.Response(200, json={"id": "cus_AAAAAA"})

    ref = asyncio.run(
        customers(handler).create_customer(name="Acme", tenant_id="t-1", idempotency_key="k-1")
    )
    assert ref == "cus_AAAAAA" and len(seen) == 3
    assert {r.headers["Idempotency-Key"] for r in seen} == {"k-1"}


def test_a_write_without_a_key_gets_one_and_keeps_it_across_attempts() -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        if len(seen) == 1:
            return httpx.Response(503)
        return httpx.Response(
            200, json={"id": "cs_test_1", "url": "https://checkout.stripe.test/x"}
        )

    url = asyncio.run(
        customers(handler).start_setup(
            customer_ref="cus_AAAAAA",
            success_url="https://c.test/ok",
            cancel_url="https://c.test/no",
        )
    )
    assert url.startswith("https://checkout") and len(seen) == 2
    keys = {r.headers["Idempotency-Key"] for r in seen}
    assert len(keys) == 1 and next(iter(keys))


def test_it_gives_up_after_the_pauses_and_says_the_provider_is_unavailable() -> None:
    calls = 0

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ConnectTimeout("down", request=req)

    with pytest.raises(PaymentSetupError) as e:
        asyncio.run(customers(handler).current_method("cus_AAAAAA"))
    assert e.value.code == "PROVIDER_UNAVAILABLE" and calls == 3


def test_a_refusal_from_stripe_is_not_retried() -> None:
    calls = 0

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(400, json={"error": {"code": "resource_missing"}})

    with pytest.raises(PaymentSetupError) as e:
        asyncio.run(customers(handler).current_method("cus_AAAAAA"))
    assert e.value.code == "RESOURCE_MISSING" and calls == 1
