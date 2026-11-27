"""Self-serve plan purchase (CR-147), against the real database.

A company buys a published plan from the approved price book. The order waits for payment and the plan does not change until a payment of exactly the asked
amount is recorded and the order is activated again. The figure comes from the book, a price that moved is refused, and a second press reuses the order.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import pytest
from conftest import ctx, engine
from sqlalchemy import text
from test_orders_pg import setup, uid
from zid_db import tenant_transaction
from zid_platform.modules.commercial.application import activation_service as activations
from zid_platform.modules.commercial.application import orders_service as orders
from zid_platform.modules.commercial.application import self_serve_service as self_serve
from zid_platform.modules.commercial.infrastructure import catalogue_store

D = Decimal


async def buy(
    c: Any, tenant: str, plan: str, *, principal: str, amount: str, **kw: Any
) -> dict[str, Any]:
    return await self_serve.purchase(
        c,
        tenant,
        plan,
        expected_amount=amount,
        expected_currency="USD",
        principal=principal,
        legal_name=kw.get("legal_name", "Acme Ltd"),
        country=kw.get("country", "GB"),
    )


def test_a_plan_is_bought_from_the_approved_book_and_switches_only_after_it_is_paid(
    rt_url: str, owner_url: str
) -> None:
    cco, cfo, founder, buyer = uid(), uid(), uid(), uid()
    tenant = uid()

    async def run() -> None:
        await setup(rt_url, owner_url, cco=cco, cfo=cfo, founder=founder)
        async with engine(rt_url) as eng:
            # the offer is the book's own figure, and the plans that are not self-serve say so
            async with eng.begin() as c:
                offer = await self_serve.offer(c, tenant, "business")
                assert (offer["plan"], offer["amount"], offer["currency"]) == (
                    "BUSINESS",
                    "1499.00",
                    "USD",
                )
                for code in ("DEVELOPER", "ENTERPRISE", "NOPE"):
                    with pytest.raises(self_serve.SelfServeError) as e:
                        await self_serve.offer(c, tenant, code)
                    assert e.value.code == "PLAN_NOT_SELF_SERVE"

            # a price the buyer did not review is refused and nothing is created
            async with eng.begin() as c:
                with pytest.raises(self_serve.SelfServeError) as e:
                    await buy(c, tenant, "BUSINESS", principal=buyer, amount="999.00")
                assert e.value.code == "PRICE_CHANGED"
                assert await orders.account_of(c, tenant) is None

            # a company with no billing account must say who the invoice is for
            async with eng.begin() as c:
                with pytest.raises(self_serve.SelfServeError) as e:
                    await buy(
                        c, tenant, "BUSINESS", principal=buyer, amount="1499.00", legal_name=None
                    )
                assert e.value.code == "BILLING_DETAILS_REQUIRED"

            # the purchase holds the order for payment at the exact list price; no plan changes
            async with eng.begin() as c:
                held = await buy(c, tenant, "BUSINESS", principal=buyer, amount="1499.00")
            oid = held["order_id"]
            assert held["amount"] == "1499.00" and held["reused"] is False
            async with tenant_transaction(eng, ctx(tenant)) as t:
                assert await catalogue_store.active_entitlement(t) is None
                waiting = await activations.waiting(t)
            assert [(w["order_id"], w["status"], w["plan"], w["amount"]) for w in waiting] == [
                (oid, "AWAITING", "BUSINESS", "1499.00")
            ]
            async with eng.begin() as c:
                assert (await orders.order_view(c, oid))["status"] == "AWAITING_PAYMENT"
                # a plan fee has one price everywhere: there is no region to choose, and the quote is not held to the regional release gate
                region = (
                    await c.execute(
                        text(
                            "SELECT q.region FROM commercial.quote q JOIN commercial.sales_order o USING (quote_id) WHERE o.order_id = :o"
                        ),
                        {"o": uuid.UUID(oid)},
                    )
                ).scalar_one()
                assert region == "global"

            # pressing the button again reuses the waiting order: one order, one payment
            async with eng.begin() as c:
                again = await buy(c, tenant, "BUSINESS", principal=buyer, amount="1499.00")
                count = (
                    await c.execute(
                        text("SELECT count(*) FROM commercial.sales_order WHERE tenant_id = :t"),
                        {"t": uuid.UUID(tenant)},
                    )
                ).scalar_one()
            assert again["order_id"] == oid and again["reused"] is True and count == 1

            # a wrong amount is refused; only the exact one is recorded, and then the next activation switches the plan
            async with eng.begin() as c:
                with pytest.raises(activations.ActivationError) as e:
                    await activations.record_payment(
                        c,
                        tenant,
                        oid,
                        payment_ref=f"pi_ss_short_{oid}",
                        amount_minor=100_00,
                        currency="USD",
                    )
                assert e.value.code == "ACTIVATION_AMOUNT_MISMATCH"
            async with tenant_transaction(eng, ctx(tenant)) as t:
                assert await catalogue_store.active_entitlement(t) is None
            async with eng.begin() as c:
                await activations.record_payment(
                    c, tenant, oid, payment_ref=f"pi_ss_{oid}", amount_minor=1499_00, currency="USD"
                )
                done = await orders.activate(c, oid, actor=buyer, direct=True, require_payment=True)
            assert done["status"] == "ACTIVATED"
            async with tenant_transaction(eng, ctx(tenant)) as t:
                assert (await catalogue_store.active_entitlement(t))["plan_code"] == "BUSINESS"

            # now on Business: the same plan, and a smaller one, cannot be bought here; a bigger one can
            async with eng.begin() as c:
                for code, expect in (
                    ("BUSINESS", "ALREADY_ON_PLAN"),
                    ("LAUNCH", "DOWNGRADE_NOT_SELF_SERVE"),
                ):
                    with pytest.raises(self_serve.SelfServeError) as e:
                        await self_serve.offer(c, tenant, code)
                    assert e.value.code == expect
                up = await self_serve.offer(c, tenant, "SCALE")
                assert up["amount"] == "4999.00" and up["current_plan"] == "BUSINESS"

    import asyncio

    asyncio.run(run())
