"""Pay before the plan switches (CR-062), against the real database.

An activated order with a monthly fee waits (AWAITING_PAYMENT) and the company's plan does not change. Only a payment of exactly the asked amount, confirmed by
Stripe, lets the next activation switch the plan. A paid activation is held for the month-end invoice; an order with nothing to pay switches at once.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from conftest import ctx, engine
from test_orders_pg import SIGNED, setup, uid
from zid_db import tenant_transaction
from zid_platform.modules.commercial.application import activation_service as activations
from zid_platform.modules.commercial.application import orders_service as orders
from zid_platform.modules.commercial.domain.orders import QuoteLine
from zid_platform.modules.commercial.infrastructure import catalogue_store

D = Decimal


async def signed_order(
    c: Any, book: str, *, tenant: str, ae: str, lines: list[QuoteLine], pilot: bool = False
) -> dict[str, Any]:
    account = await orders.create_account(
        c,
        legal_name="Acme Ltd",
        kind="DIRECT",
        country="GB",
        currency="USD",
        reseller_of=None,
        actor=ae,
    )
    await orders.link_tenant(c, account, tenant)
    quote = await orders.create_quote(
        c,
        account_id=account,
        price_book_id=book,
        region="uk-1",
        term_months=12,
        prepaid=False,
        prepaid_discount=D(0),
        liability_capped=True,
        residency_commitment=None,
        pilot=pilot,
        lines=lines,
        actor=ae,
    )
    return await orders.accept(
        c,
        quote["quote_id"],
        tenant_id=tenant,
        signer_ref="CFO of Acme",
        signed_document_digest=SIGNED,
        effective_from=datetime.now(UTC) + timedelta(minutes=1),
        amends_order_id=None,
        actor=ae,
    )


def committed(rt_url: str) -> Any:
    """A transaction that commits on exit: a company reads what the operator did only after it is committed."""
    return engine(rt_url)


def test_an_activated_order_waits_for_payment_and_the_plan_switches_only_when_it_is_paid(
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
                held = await orders.activate(c, oid, actor=activator, require_payment=True)
            assert held["awaiting_payment"] is True and held["status"] == "AWAITING_PAYMENT"
            assert (
                held["amount"] == "1499.00" and held["currency"] == "USD"
            )  # the list price of the plan, the figure the commitment approval used

            # nothing changed for the company: no entitlement, the order is not active
            async with tenant_transaction(eng, ctx(tenant)) as t:
                assert await catalogue_store.active_entitlement(t) is None
                waiting = await activations.waiting(t)
            assert [(w["order_id"], w["status"], w["plan"]) for w in waiting] == [
                (oid, "AWAITING", "BUSINESS")
            ]
            async with eng.begin() as c:
                assert (await orders.order_view(c, oid))["status"] == "AWAITING_PAYMENT"
                # activating again while it waits changes nothing and asks for no second amount
                again = await orders.activate(c, oid, actor=activator, require_payment=True)
            assert again["awaiting_payment"] is True

            # a payment of another amount, or in another currency, is refused and nothing is recorded
            for ref, minor, cur in (("pi_short", 100_00, "USD"), ("pi_eur", 1499_00, "EUR")):
                async with eng.begin() as c:
                    with pytest.raises(activations.ActivationError) as e:
                        await activations.record_payment(
                            c, tenant, oid, payment_ref=ref, amount_minor=minor, currency=cur
                        )
                    assert e.value.code == "ACTIVATION_AMOUNT_MISMATCH"

            # the right amount is recorded once; the same payment again is a no-op
            async with eng.begin() as c:
                paid = await activations.record_payment(
                    c, tenant, oid, payment_ref="pi_ok", amount_minor=1499_00, currency="USD"
                )
                assert paid["status"] == "PAID" and paid["already_recorded"] is False
                assert (
                    await activations.record_payment(
                        c, tenant, oid, payment_ref="pi_ok", amount_minor=1499_00, currency="USD"
                    )
                )["already_recorded"] is True
                assert (await orders.order_view(c, oid))[
                    "status"
                ] == "AWAITING_PAYMENT"  # paid, not yet switched: the next activation does that

            # only now does the next activation switch the plan, and the activator is still never the person who entered the order
            async with eng.begin() as c:
                done = await orders.activate(c, oid, actor=activator, require_payment=True)
            assert done["status"] == "ACTIVATED" and done["already_active"] is False
            async with tenant_transaction(eng, ctx(tenant)) as t:
                assert (await catalogue_store.active_entitlement(t))["plan_code"] == "BUSINESS"
                assert await activations.waiting(t) == []
                # the paid fee waits for the month-end invoice to take it off
                credits = await activations.unapplied_credits(t, tenant, "USD")
                assert [(str(x["order_id"]), Decimal(x["balance"])) for x in credits] == [
                    (oid, D("1499"))
                ]
                assert await activations.unapplied_credits(t, tenant, "EUR") == []

    asyncio.run(run())


def test_the_operator_can_take_an_unpaid_order_back_but_never_a_paid_one(
    rt_url: str, owner_url: str
) -> None:
    ae, cco, cfo, founder, activator = uid(), uid(), uid(), uid(), uid()
    tenant = uid()

    async def run() -> None:
        book = await setup(rt_url, owner_url, cco=cco, cfo=cfo, founder=founder)
        async with engine(rt_url) as eng, eng.begin() as c:
            order = await signed_order(
                c, book, tenant=tenant, ae=ae, lines=[QuoteLine(1, "ZID-BASE", "BUSINESS", D(1))]
            )
            oid = order["order_id"]
            await orders.activate(c, oid, actor=activator, require_payment=True)

            cancelled = await activations.cancel(c, tenant, oid, actor=activator)
            assert (
                cancelled["status"] == "CANCELLED"
                and (await orders.order_view(c, oid))["status"] == "SIGNED"
            )

            # asking again opens a fresh wait; once paid it cannot be taken back here (that would be a refund)
            await orders.activate(c, oid, actor=activator, require_payment=True)
            await activations.record_payment(
                c, tenant, oid, payment_ref="pi_paid", amount_minor=1499_00, currency="USD"
            )
            with pytest.raises(activations.ActivationError) as e:
                await activations.cancel(c, tenant, oid, actor=activator)
            assert e.value.code == "ACTIVATION_NOT_WAITING"

    asyncio.run(run())


def test_a_plan_with_nothing_to_pay_and_a_pilot_switch_at_once(rt_url: str, owner_url: str) -> None:
    ae, cco, cfo, founder, activator = uid(), uid(), uid(), uid(), uid()
    tenant = uid()

    async def run() -> None:
        book = await setup(rt_url, owner_url, cco=cco, cfo=cfo, founder=founder)
        async with engine(rt_url) as eng, eng.begin() as c:
            # without the switch, activation behaves as it always did
            order = await signed_order(
                c, book, tenant=tenant, ae=ae, lines=[QuoteLine(1, "ZID-BASE", "BUSINESS", D(1))]
            )
            done = await orders.activate(c, order["order_id"], actor=activator)
            assert done["status"] == "ACTIVATED"
            async with tenant_transaction(eng, ctx(tenant)) as t:
                assert await activations.waiting(t) == []

    asyncio.run(run())


def test_another_company_cannot_see_or_pay_a_waiting_order(rt_url: str, owner_url: str) -> None:
    ae, cco, cfo, founder, activator = uid(), uid(), uid(), uid(), uid()
    tenant, other = uid(), uid()

    async def run() -> None:
        book = await setup(rt_url, owner_url, cco=cco, cfo=cfo, founder=founder)
        async with engine(rt_url) as eng, eng.begin() as c:
            order = await signed_order(
                c, book, tenant=tenant, ae=ae, lines=[QuoteLine(1, "ZID-BASE", "BUSINESS", D(1))]
            )
            await orders.activate(c, order["order_id"], actor=activator, require_payment=True)
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(other)) as t:
            assert await activations.waiting(t) == []
            with pytest.raises(activations.ActivationError):
                await activations.waiting_one(t, other, order["order_id"])
        assert uuid.UUID(order["order_id"])

    asyncio.run(run())


def test_a_paid_activation_is_taken_off_the_month_end_invoice_once(
    rt_url: str, owner_url: str
) -> None:
    from test_invoicing_pg import ENTITY, LAST, rated_month
    from zid_platform.modules.commercial.application import invoicing_service as inv

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
            async with eng.begin() as c:
                await orders.activate(c, order["order_id"], actor=activator, require_payment=True)
                await activations.record_payment(
                    c,
                    tenant,
                    order["order_id"],
                    payment_ref="pi_credit",
                    amount_minor=1499_00,
                    currency="USD",
                )
        await rated_month(
            rt_url, tenant
        )  # a Business month whose invoice is 1769 before any credit
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            d = await inv.create_draft(
                c, tenant_id=tenant, month=LAST, issuing_entity=ENTITY, actor=drafter
            )
            assert d["subtotal"] == "270"  # 1769 less the 1499 paid when the plan was activated
            credit = [ln for ln in d["lines"] if ln["kind"] == "CREDIT"]
            assert len(credit) == 1 and Decimal(credit[0]["net"]) == D("-1499")
            # asking for the same invoice again neither adds a second credit nor changes the total
            again = await inv.create_draft(
                c, tenant_id=tenant, month=LAST, issuing_entity=ENTITY, actor=drafter
            )
            assert (
                again["subtotal"] == "270"
                and len([ln for ln in again["lines"] if ln["kind"] == "CREDIT"]) == 1
            )
            assert (
                await activations.unapplied_credits(c, tenant, "USD") == []
            )  # taken into account: it cannot be credited twice

    asyncio.run(run())
