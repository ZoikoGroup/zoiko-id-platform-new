"""Pay before the plan switches (CR-062), the parts around the payment: credit across invoices, refunds and the processor's own report."""

from __future__ import annotations

import asyncio
import json
import uuid
from decimal import Decimal
from typing import Any

import pytest
from conftest import ctx, engine
from sqlalchemy import text
from test_activation_payment_pg import D, signed_order
from test_orders_pg import setup, uid
from zid_db import tenant_transaction
from zid_platform.modules.commercial.application import activation_service as activations
from zid_platform.modules.commercial.application import invoicing_service as inv
from zid_platform.modules.commercial.application import orders_service as orders
from zid_platform.modules.commercial.application import stripe_webhook_service as webhook
from zid_platform.modules.commercial.domain import stripe_webhook as hook
from zid_platform.modules.commercial.domain.orders import QuoteLine
from zid_platform.modules.commercial.infrastructure import catalogue_store


def test_a_credit_larger_than_the_invoice_carries_over_and_a_voided_draft_gives_it_back(
    rt_url: str, owner_url: str
) -> None:
    from test_invoicing_pg import ENTITY, LAST, rated_month

    ae, cco, cfo, founder, activator, drafter = uid(), uid(), uid(), uid(), uid(), uid()
    tenant = uid()

    async def run() -> None:
        book = await setup(rt_url, owner_url, cco=cco, cfo=cfo, founder=founder)
        async with engine(rt_url) as eng:
            async with eng.begin() as c:
                order = await signed_order(
                    c,
                    book,
                    tenant=tenant,
                    ae=ae,
                    lines=[QuoteLine(1, "ZID-BASE", "BUSINESS", D(1))],
                )
            oid = order["order_id"]
            async with eng.begin() as c:
                await orders.activate(c, oid, actor=activator, require_payment=True)
                await activations.record_payment(
                    c, tenant, oid, payment_ref="pi_big", amount_minor=1499_00, currency="USD"
                )
        # the company paid a whole month but the next invoice is smaller (a part month): make the payment larger than the invoice
        async with engine(owner_url) as eng, eng.begin() as c:
            await c.execute(
                text("UPDATE commercial.activation_payment SET amount = 5000 WHERE order_id = :o"),
                {"o": uuid.UUID(oid)},
            )
        await rated_month(rt_url, tenant)
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            d = await inv.create_draft(
                c, tenant_id=tenant, month=LAST, issuing_entity=ENTITY, actor=drafter
            )
            assert d["subtotal"] == "0"  # the whole invoice was covered
            credit = [ln for ln in d["lines"] if ln["kind"] == "CREDIT"]
            assert len(credit) == 1 and Decimal(credit[0]["net"]) == D("-1769")  # only what fits
            left = await activations.unapplied_credits(c, tenant, "USD")
            assert [(str(x["order_id"]), Decimal(x["balance"])) for x in left] == [
                (oid, D("3231"))
            ]  # the rest carries to the next invoice
            await inv.void(c, d["invoice_id"])  # voiding the draft gives everything back
            back = await activations.unapplied_credits(c, tenant, "USD")
            assert [(str(x["order_id"]), Decimal(x["balance"])) for x in back] == [(oid, D("5000"))]

    asyncio.run(run())


def test_a_paid_unused_activation_can_be_marked_for_refund_and_the_processor_report_closes_it(
    rt_url: str, owner_url: str
) -> None:
    ae, cco, cfo, founder, activator = uid(), uid(), uid(), uid(), uid()
    tenant = uid()

    async def run() -> None:
        book = await setup(rt_url, owner_url, cco=cco, cfo=cfo, founder=founder)
        async with engine(rt_url) as eng:
            async with eng.begin() as c:
                order = await signed_order(
                    c,
                    book,
                    tenant=tenant,
                    ae=ae,
                    lines=[QuoteLine(1, "ZID-BASE", "BUSINESS", D(1))],
                )
            oid = order["order_id"]
            async with eng.begin() as c:
                await orders.activate(c, oid, actor=activator, require_payment=True)
                with pytest.raises(
                    activations.ActivationError
                ) as e:  # nothing paid yet: nothing to return
                    await activations.request_refund(
                        c, tenant, oid, actor=activator, reason="customer withdrew"
                    )
                assert e.value.code == "ACTIVATION_REFUND_NOT_ALLOWED"
                await activations.record_payment(
                    c, tenant, oid, payment_ref="pi_refund", amount_minor=1499_00, currency="USD"
                )
            async with eng.begin() as c:
                with pytest.raises(activations.ActivationError):  # a reason is required
                    await activations.request_refund(c, tenant, oid, actor=activator, reason=" ")
            async with eng.begin() as c:
                due = await activations.request_refund(
                    c,
                    tenant,
                    oid,
                    actor=activator,
                    reason="customer withdrew before the plan switched",
                )
                assert due["status"] == "REFUND_DUE" and due["payment_ref"] == "pi_refund"
                assert (
                    await activations.request_refund(
                        c, tenant, oid, actor=activator, reason="again"
                    )
                )["already_requested"] is True
                queue = await activations.refunds_due(c)
                assert [
                    (q["order_id"], q["payment_ref"], q["amount"])
                    for q in queue
                    if q["order_id"] == oid
                ] == [(oid, "pi_refund", "1499.00")]
            async with tenant_transaction(eng, ctx(tenant)) as t:
                assert (
                    await activations.unapplied_credits(t, tenant, "USD") == []
                )  # never taken off an invoice now

            # the processor reports the refund of that payment: the activation payment is closed
            raw = json.dumps(
                {
                    "id": "evt_act_refund",
                    "type": "charge.refunded",
                    "data": {
                        "object": {
                            "id": "ch_1",
                            "payment_intent": "pi_refund",
                            "amount_refunded": 1499_00,
                            "currency": "usd",
                        }
                    },
                }
            ).encode()
            assert await webhook.process(eng, hook.parse_event(raw)) == "APPLIED"
            async with eng.begin() as c:
                assert [q for q in await activations.refunds_due(c) if q["order_id"] == oid] == []
            async with tenant_transaction(eng, ctx(tenant)) as t:
                row = await activations.load(t, tenant, oid)
                assert row["status"] == "REFUNDED" and row["refunded_at"] is not None

    asyncio.run(run())


def test_stripe_reporting_the_payment_switches_the_plan_even_if_the_buyer_never_returns(
    rt_url: str, owner_url: str
) -> None:
    ae, cco, cfo, founder, activator = uid(), uid(), uid(), uid(), uid()
    tenant = uid()
    customer = f"cus_{uuid.uuid4().hex[:12]}"

    def event(event_id: str, *, who: str, amount: int = 1499_00, oid: str) -> Any:
        obj = {
            "id": "pi_hook_act",
            "customer": who,
            "amount": amount,
            "currency": "usd",
            "metadata": {"order_id": oid, "purpose": "plan_activation"},
        }
        return hook.parse_event(
            json.dumps(
                {"id": event_id, "type": "payment_intent.succeeded", "data": {"object": obj}}
            ).encode()
        )

    async def run() -> None:
        book = await setup(rt_url, owner_url, cco=cco, cfo=cfo, founder=founder)
        async with engine(rt_url) as eng:
            async with eng.begin() as c:
                order = await signed_order(
                    c,
                    book,
                    tenant=tenant,
                    ae=ae,
                    lines=[QuoteLine(1, "ZID-BASE", "BUSINESS", D(1))],
                )
                account = (await inv.account_of_tenant(c, tenant))["account_id"]  # type: ignore[index]
                await inv.set_payment_customer(
                    c, account, provider="stripe", customer_ref=customer, actor=ae
                )
            oid = order["order_id"]
            async with eng.begin() as c:
                await orders.activate(c, oid, actor=activator, require_payment=True)

            # another customer's payment naming this order is not followed; a short payment is flagged, not applied
            assert (
                await webhook.process(eng, event("evt_act_other", who="cus_someoneelse", oid=oid))
                == "UNMATCHED"
            )
            assert (
                await webhook.process(
                    eng, event("evt_act_short", who=customer, amount=100_00, oid=oid)
                )
                == "NEEDS_REVIEW"
            )
            async with tenant_transaction(eng, ctx(tenant)) as t:
                assert await catalogue_store.active_entitlement(t) is None

            # the real payment records it and switches the plan; a repeat of the same payment changes nothing
            assert (
                await webhook.process(eng, event("evt_act_ok", who=customer, oid=oid)) == "APPLIED"
            )
            async with tenant_transaction(eng, ctx(tenant)) as t:
                assert (await catalogue_store.active_entitlement(t))["plan_code"] == "BUSINESS"
            assert (
                await webhook.process(eng, event("evt_act_again", who=customer, oid=oid))
                == "IGNORED"
            )
            assert (
                await webhook.process(eng, event("evt_act_ok", who=customer, oid=oid))
                == "DUPLICATE"
            )

    asyncio.run(run())
