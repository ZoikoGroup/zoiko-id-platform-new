"""A price for one company (CR-148), against the real database.

A company can buy a plan at the list price straight away, or ask for a different price. The team answers with an offer that is for that one company only, never above the list price, and
that ends on a date. The company then buys at that amount and pays it; the plan switches only after exactly that payment is confirmed, and the offer is spent when it does.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from conftest import ctx, engine
from sqlalchemy import text
from test_orders_pg import setup, uid
from zid_db import tenant_transaction
from zid_platform.modules.commercial.application import activation_service as activations
from zid_platform.modules.commercial.application import orders_service as orders
from zid_platform.modules.commercial.application import plan_offer_service as offers
from zid_platform.modules.commercial.application import plan_request_service as requests
from zid_platform.modules.commercial.application import self_serve_service as self_serve
from zid_platform.modules.commercial.domain.plan_requests import PlanRequestError
from zid_platform.modules.commercial.infrastructure import catalogue_store
from zid_platform.modules.commercial.infrastructure.plan_request_store import operator_scope

D = Decimal
DIRECT = "00000000-0000-4000-8000-0000000000d1"


async def ask(eng: Any, tenant: str, plan: str, *, want: str | None = None) -> str:
    async with tenant_transaction(eng, ctx(tenant)) as t:
        made = await requests.request_plan(
            t,
            tenant,
            str(uuid.uuid4()),
            plan,
            "Can we have a better price?",
            D(want) if want else None,
        )
    return str(made["request_id"])


async def make(
    eng: Any,
    request_id: str,
    amount: str,
    *,
    direct: bool = True,
    days: int = 14,
    now: datetime | None = None,
) -> dict[str, Any]:
    async with eng.begin() as c, operator_scope(c):
        return await offers.make_offer(
            c,
            request_id,
            amount=D(amount),
            valid_days=days,
            note="Agreed on the call",
            operator_id=str(uuid.uuid4()),
            direct=direct,
            direct_approver=DIRECT,
            policy_lock=False,
            now=now,
        )


async def my_price(eng: Any, tenant: str, plan: str) -> dict[str, Any]:
    async with tenant_transaction(eng, ctx(tenant)) as t:
        return await self_serve.offer(t, tenant, plan)


async def buy(eng: Any, tenant: str, plan: str, amount: str) -> dict[str, Any]:
    async with eng.begin() as c:
        return await self_serve.purchase(
            c,
            tenant,
            plan,
            expected_amount=amount,
            expected_currency="USD",
            principal=str(uuid.uuid4()),
            legal_name="Acme Ltd",
            country="GB",
        )


def test_a_company_buys_at_the_price_the_team_agreed_and_the_plan_switches_only_after_it_is_paid(
    rt_url: str, owner_url: str
) -> None:
    cco, cfo, founder = uid(), uid(), uid()
    tenant = uid()

    async def run() -> None:
        await setup(rt_url, owner_url, cco=cco, cfo=cfo, founder=founder)
        async with engine(rt_url) as eng:
            # before any offer the company pays the list price
            assert (await my_price(eng, tenant, "SCALE"))["amount"] == "4999.00"

            # it asks for a lower price; asking changes nothing about what it pays or what it has
            rid = await ask(eng, tenant, "SCALE", want="4000")
            assert (await my_price(eng, tenant, "SCALE"))["amount"] == "4999.00"
            async with tenant_transaction(eng, ctx(tenant)) as t:
                mine = (await requests.my_requests(t))[0]
                assert mine["desired_amount"] == "4000.00" and mine["offer"] is None
                assert await catalogue_store.active_entitlement(t) is None

            # the team answers with a price for this company only
            made = await make(eng, rid, "4500")
            assert (
                made["offer"]["amount"] == "4500.00" and made["offer"]["list_amount"] == "4999.00"
            )
            assert (
                made["awaiting_approval"] is False
            )  # direct mode: the one operator is the authority
            agreed = await my_price(eng, tenant, "SCALE")
            assert (
                agreed["negotiated"] is True
                and agreed["amount"] == "4500.00"
                and agreed["list_amount"] == "4999.00"
            )
            # it is for this company alone: another company still sees the list price
            assert (await my_price(eng, uid(), "SCALE"))["amount"] == "4999.00"

            # paying a different amount than the one reviewed is refused: the list price is no longer what was agreed
            with pytest.raises(self_serve.SelfServeError) as e:
                await buy(eng, tenant, "SCALE", "4999.00")
            assert e.value.code == "PRICE_CHANGED"

            # buying at the agreed price holds the order for exactly that amount, and the plan has not changed
            held = await buy(eng, tenant, "SCALE", "4500.00")
            assert held["amount"] == "4500.00" and held["negotiated"] is True
            oid = held["order_id"]
            async with tenant_transaction(eng, ctx(tenant)) as t:
                waiting = await activations.waiting(t)
                assert [(w["order_id"], w["status"], w["amount"]) for w in waiting] == [
                    (oid, "AWAITING", "4500.00")
                ]
                assert await catalogue_store.active_entitlement(t) is None
            async with eng.begin() as c:
                net = (
                    await c.execute(
                        text(
                            "SELECT net_price FROM commercial.sales_order_line WHERE order_id = :o AND sku_id = 'ZID-BASE'"
                        ),
                        {"o": uuid.UUID(oid)},
                    )
                ).scalar_one()
            assert Decimal(net) == D(
                "4500"
            )  # the order is for exactly the amount the company reviewed, to the cent

            # paying the list price, or any other amount, does not satisfy the order
            async with eng.begin() as c:
                with pytest.raises(activations.ActivationError) as e2:
                    await activations.record_payment(
                        c,
                        tenant,
                        oid,
                        payment_ref=f"pi_o_list_{oid}",
                        amount_minor=499_900,
                        currency="USD",
                    )
                assert e2.value.code == "ACTIVATION_AMOUNT_MISMATCH"
            async with tenant_transaction(eng, ctx(tenant)) as t:
                assert await catalogue_store.active_entitlement(t) is None

            # only the exact agreed amount, confirmed, lets the order switch the plan on; and the offer is spent
            async with eng.begin() as c:
                await activations.record_payment(
                    c,
                    tenant,
                    oid,
                    payment_ref=f"pi_o_ok_{oid}",
                    amount_minor=450_000,
                    currency="USD",
                )
                done = await orders.activate(
                    c, oid, actor=str(uuid.uuid4()), direct=True, require_payment=True
                )
            assert done["status"] == "ACTIVATED"
            async with tenant_transaction(eng, ctx(tenant)) as t:
                assert (await catalogue_store.active_entitlement(t))["plan_code"] == "SCALE"
                states = (
                    await t.execute(
                        text(
                            "SELECT offer_state FROM commercial.plan_request WHERE request_id = :r"
                        ),
                        {"r": uuid.UUID(rid)},
                    )
                ).scalar_one()
            assert states == "USED"

    asyncio.run(run())


def test_an_offer_is_never_above_the_list_price_nor_for_a_contract_plan_and_it_ends(
    rt_url: str, owner_url: str
) -> None:
    cco, cfo, founder = uid(), uid(), uid()
    tenant, other = uid(), uid()

    async def run() -> None:
        await setup(rt_url, owner_url, cco=cco, cfo=cfo, founder=founder)
        async with engine(rt_url) as eng:
            rid = await ask(eng, tenant, "BUSINESS")
            with pytest.raises(offers.OfferError) as e:
                await make(eng, rid, "1499.01")
            assert e.value.code == "OFFER_ABOVE_LIST"
            for bad in ("0", "-5"):
                with pytest.raises((offers.OfferError, ArithmeticError)):
                    await make(eng, rid, bad)
            with pytest.raises(offers.OfferError) as e:
                await make(eng, rid, "1000", days=0)
            assert e.value.code == "OFFER_VALIDITY"
            # nothing above was recorded: the request is still open and the company still pays list
            assert (await my_price(eng, tenant, "BUSINESS"))["negotiated"] is False

            # the list price itself is a valid answer and needs no authority
            same = await make(eng, rid, "1499")
            assert same["offer"]["reduction_percent"] == "0.00"
            assert (await my_price(eng, tenant, "BUSINESS"))["amount"] == "1499.00"

            # a contract plan has no online price, so no offer
            ent = await ask(eng, other, "ENTERPRISE")
            with pytest.raises(offers.OfferError) as e:
                await make(eng, ent, "9000")
            assert e.value.code == "PLAN_NOT_SELF_SERVE"

            # an offer that has ended is ignored: the company is back to the list price
            lapsed_tenant = uid()
            lapsed = await ask(eng, lapsed_tenant, "SCALE")
            await make(eng, lapsed, "4000", days=1)
            assert (await my_price(eng, lapsed_tenant, "SCALE"))[
                "negotiated"
            ] is True  # still open today
            async with engine(owner_url) as oeng, oeng.begin() as oc:  # its last day passes
                await oc.execute(
                    text(
                        "UPDATE commercial.plan_request SET offer_expires_at = :x WHERE request_id = :r"
                    ),
                    {"x": datetime.now(UTC) - timedelta(days=1), "r": uuid.UUID(lapsed)},
                )
            assert (await my_price(eng, lapsed_tenant, "SCALE"))["negotiated"] is False
            with pytest.raises(self_serve.SelfServeError) as e2:
                await buy(eng, lapsed_tenant, "SCALE", "4000.00")
            assert e2.value.code == "PRICE_CHANGED"

            # a withdrawn offer is gone from the company's side at once
            gone_tenant = uid()
            gone = await ask(eng, gone_tenant, "LAUNCH")
            await make(eng, gone, "250")
            assert (await my_price(eng, gone_tenant, "LAUNCH"))["amount"] == "250.00"
            async with eng.begin() as c, operator_scope(c):
                await offers.withdraw_offer(
                    c, gone, operator_id=str(uuid.uuid4()), reason="Agreed price changed"
                )
            assert (await my_price(eng, gone_tenant, "LAUNCH"))["amount"] == "299.00"

    asyncio.run(run())


def test_a_reduction_in_governed_mode_waits_for_a_different_person_with_the_authority(
    rt_url: str, owner_url: str
) -> None:
    cco, cfo, founder = uid(), uid(), uid()
    maker = uid()
    tenant = uid()

    async def run() -> None:
        await setup(rt_url, owner_url, cco=cco, cfo=cfo, founder=founder)
        async with engine(rt_url) as eng:
            rid = await ask(eng, tenant, "SCALE")
            async with eng.begin() as c, operator_scope(c):
                made = await offers.make_offer(
                    c,
                    rid,
                    amount=D("4500"),
                    valid_days=14,
                    note=None,
                    operator_id=maker,
                    direct=False,
                    direct_approver=DIRECT,
                    policy_lock=False,
                )
            assert (
                made["awaiting_approval"] is True and made["offer"]["state"] == "PENDING_APPROVAL"
            )
            # until it is approved the company sees only the list price, and cannot buy at the reduced one
            assert (await my_price(eng, tenant, "SCALE"))["negotiated"] is False
            with pytest.raises(self_serve.SelfServeError):
                await buy(eng, tenant, "SCALE", "4500.00")

            async def approve(who: str) -> dict[str, Any]:
                async with eng.begin() as c, operator_scope(c):
                    return await offers.approve_offer(c, rid, approver_id=who, policy_lock=False)

            with pytest.raises(offers.OfferError) as e:
                await approve(maker)  # the maker never approves their own offer
            assert e.value.code == "OFFER_SELF_APPROVAL"
            with pytest.raises(offers.OfferError) as e:
                await approve(uid())  # someone who does not hold the role the reduction needs
            assert e.value.code == "APPROVER_ROLE_MISSING"
            approved = await approve(cco)  # about 10% is the CCO's band
            assert approved["offer"]["state"] == "OPEN"
            agreed = await my_price(eng, tenant, "SCALE")
            assert agreed["negotiated"] is True and agreed["amount"] == "4500.00"
            with pytest.raises(offers.OfferError) as e:
                await approve(cco)  # approving twice is refused
            assert e.value.code == "OFFER_NOT_AWAITING_APPROVAL"
            # and the company can now buy at it, with the quote carrying the approval the CCO gave
            held = await buy(eng, tenant, "SCALE", "4500.00")
            assert held["amount"] == "4500.00"

            # a reduction whose band needs two different roles is refused, never approved with less authority than the band asks for
            big_tenant = uid()
            big = await ask(eng, big_tenant, "SCALE")
            with pytest.raises(offers.OfferError) as e:
                async with eng.begin() as c, operator_scope(c):
                    await offers.make_offer(
                        c,
                        big,
                        amount=D("3000"),
                        valid_days=14,
                        note=None,
                        operator_id=maker,
                        direct=False,
                        direct_approver=DIRECT,
                        policy_lock=False,
                    )
            assert e.value.code == "OFFER_NEEDS_SEVERAL_ROLES"

    asyncio.run(run())


def test_a_request_for_a_price_must_be_a_real_amount(rt_url: str, owner_url: str) -> None:
    cco, cfo, founder = uid(), uid(), uid()
    tenant = uid()

    async def run() -> None:
        await setup(rt_url, owner_url, cco=cco, cfo=cfo, founder=founder)
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as t:
            for bad in (D(0), D(-1), D(2_000_000)):
                with pytest.raises(PlanRequestError):
                    await requests.request_plan(t, tenant, str(uuid.uuid4()), "BUSINESS", None, bad)
            # no amount is fine: a company may simply ask
            made = await requests.request_plan(
                t, tenant, str(uuid.uuid4()), "BUSINESS", "Please call me", None
            )
            assert made["status"] == "OPEN"

    asyncio.run(run())
