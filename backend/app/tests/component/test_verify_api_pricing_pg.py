"""The operator-set price of a Verify API check (CR-161), against the real database: propose, a different operator approves, and the company's usage screen shows an indicative cost with the price named.

Prices are the same for every company, so a test removes its own rows afterwards (the runtime role can never delete) and leaves the shared price list as it found it.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, date, datetime
from typing import Any

import pytest
from conftest import engine
from sqlalchemy import text
from test_verify_api_go_live_pg import Company, code, operator_tx, uid
from zid_platform.modules.verify_api.application import pricing_service as svc
from zid_platform.modules.verify_api.domain import model as m
from zid_platform.modules.verify_api.domain import pricing as p

NOW = datetime.now(UTC)
START = p.first_of_next_month(NOW.date())
MONTH_AFTER = p.first_of_next_month(START)


def proposal(**over: Any) -> dict[str, Any]:
    return {
        "meter": "VERIFY_CHECK_COMPLETE",
        "currency": "USD",
        "unit_rate": "1.250000",
        "effective_from": START.isoformat(),
        **over,
    }


def clean(owner_url: str) -> None:
    async def go() -> None:
        async with engine(owner_url) as eng, eng.begin() as c:
            await c.execute(text("DELETE FROM verify.api_price"))

    asyncio.run(go())


def test_a_different_operator_approves_a_price_that_starts_next_month_and_nothing_is_ever_repriced(
    rt_url: str, owner_url: str
) -> None:
    clean(owner_url)
    op1, op2 = uid(), uid()

    async def run() -> None:
        async with operator_tx(rt_url) as c:
            # a price that would start this month, or sit mid-month, or be a float is refused
            for bad, why in (
                (
                    proposal(effective_from=date(NOW.year, NOW.month, 1).isoformat()),
                    "PRICE_NOT_IN_FUTURE",
                ),
                (proposal(effective_from=START.replace(day=2).isoformat()), "VALIDATION_FAILED"),
                (proposal(unit_rate=1.25), "VALIDATION_FAILED"),
                (proposal(currency="XYZ"), "VALIDATION_FAILED"),
                (proposal(meter="NOPE"), "VALIDATION_FAILED"),
            ):
                with pytest.raises(m.PartnerError) as e:
                    await svc.propose(c, op1, bad)
                assert e.value.code == why, bad
        async with operator_tx(rt_url) as c:
            made = await svc.propose(c, op1, proposal())
            assert (
                made["status"] == "PROPOSED"
                and made["unit_rate"] == "1.25"
                and made["decided_by"] is None
            )
        pid = made["price_id"]
        async with operator_tx(rt_url) as c:  # the proposer cannot decide it, either way
            for fn in (svc.approve, svc.reject):
                with pytest.raises(m.PartnerError) as e:
                    await fn(c, pid, op1, "I would like this one.")
                assert e.value.code == "PRICE_SELF_APPROVAL"
        async with operator_tx(rt_url) as c:  # a rejection needs a reason
            with pytest.raises(m.PartnerError) as e:
                await svc.reject(c, pid, op2, " ")
            assert e.value.code == "NOTE_REQUIRED"
        async with operator_tx(rt_url) as c:
            done = await svc.approve(c, pid, op2, "Agreed with finance.")
            assert done["status"] == "APPROVED" and done["decided_by"] == op2
        async with operator_tx(rt_url) as c:  # a decided price is closed
            with pytest.raises(m.PartnerError) as e:
                await svc.approve(c, pid, op2, None)
            assert e.value.code == "PRICE_STATE_INVALID"
        # a second approved price for the same meter, currency and month is refused; a later month is how it changes
        async with operator_tx(rt_url) as c:
            dup = await svc.propose(c, op1, proposal(unit_rate="2"))
        async with operator_tx(rt_url) as c:
            with pytest.raises(m.PartnerError) as e:
                await svc.approve(c, dup["price_id"], op2, None)
            assert e.value.code == "PRICE_EXISTS"
        async with operator_tx(rt_url) as c:
            later = await svc.propose(
                c, op1, proposal(unit_rate="1.5", effective_from=MONTH_AFTER.isoformat())
            )
        async with operator_tx(rt_url) as c:
            assert (await svc.approve(c, later["price_id"], op2, None))["status"] == "APPROVED"
        async with operator_tx(rt_url) as c:
            listing = await svc.list_view(c)
            assert {i["status"] for i in listing["items"]} == {"APPROVED", "PROPOSED"}
            assert "USD" in listing["currencies"] and {x["id"] for x in listing["meters"]} == set(
                p.METERS
            )

    try:
        asyncio.run(run())
    finally:
        clean(owner_url)


def test_the_database_itself_refuses_a_self_approved_price_and_a_mid_month_start(
    rt_url: str, owner_url: str
) -> None:
    clean(owner_url)

    async def run() -> None:
        async with engine(owner_url) as eng:
            who = uuid.uuid4()
            for sql, params in (
                (
                    "INSERT INTO verify.api_price (price_id, meter, currency, unit_rate, effective_from, status, proposed_by, decided_by) VALUES (:i, 'VERIFY_CHECK_COMPLETE', 'USD', 1, :d, 'APPROVED', :w, :w)",
                    {"i": uuid.uuid4(), "d": START, "w": who},
                ),
                (
                    "INSERT INTO verify.api_price (price_id, meter, currency, unit_rate, effective_from, proposed_by) VALUES (:i, 'VERIFY_CHECK_COMPLETE', 'USD', 1, :d, :w)",
                    {"i": uuid.uuid4(), "d": START.replace(day=2), "w": who},
                ),
            ):
                with pytest.raises(Exception, match="check constraint|violates"):
                    async with eng.begin() as c:
                        await c.execute(text(sql), params)

    try:
        asyncio.run(run())
    finally:
        clean(owner_url)


def test_a_company_sees_an_indicative_cost_with_the_price_named_and_only_from_the_month_the_price_starts(
    rt_url: str, owner_url: str
) -> None:
    clean(owner_url)
    co = Company(rt_url)
    op1, op2 = uid(), uid()
    try:

        async def price() -> None:
            async with operator_tx(rt_url) as c:
                one = await svc.propose(c, op1, proposal(unit_rate="2.50"))
            async with operator_tx(rt_url) as c:
                await svc.approve(c, one["price_id"], op2, None)

        asyncio.run(price())
        # this month has no price in force: the company sees counts only, as before
        now_month = f"{NOW:%Y-%m}"
        this = co.http.get(f"/v1/verify-api/usage?month={now_month}", headers=co.h).json()
        assert this["billing"]["state"] == "USAGE_FACTS_ONLY" and this["billing"]["cost"] == []
        # the month the price starts shows the cost, even with no usage: every line at zero, the price named
        nxt = co.http.get(f"/v1/verify-api/usage?month={START:%Y-%m}", headers=co.h).json()
        assert (
            nxt["billing"]["state"] == "INDICATIVE_COST"
            and "not an invoice" in nxt["billing"]["note"]
        )
        usd = nxt["billing"]["cost"][0]
        assert usd["currency"] == "USD" and usd["complete"] is True and usd["total"] == "0.00"
        assert usd["lines"] == [
            {
                "meter": "VERIFY_CHECK_COMPLETE",
                "label": "A production check completed with a final result",
                "units": 0,
                "unit_rate": "2.5",
                "amount": "0.00",
                "price_from": START.isoformat(),
            }
        ]
        # a bad month is still refused, and nobody else's numbers come into it
        assert (
            code(co.http.get("/v1/verify-api/usage?month=nope", headers=co.h))
            == "VALIDATION_FAILED"
        )
    finally:
        co.close()
        clean(owner_url)


def test_the_operator_routes_for_prices_refuse_anyone_who_is_not_an_operator(
    rt_url: str, owner_url: str
) -> None:
    co = Company(rt_url)
    try:
        assert co.http.get("/operator/verify-api/prices").status_code in (401, 403)
        assert co.http.get("/operator/verify-api/prices", headers=co.h).status_code in (401, 403)
        assert co.http.post(
            "/operator/verify-api/prices", headers=co.h, json=proposal()
        ).status_code in (401, 403)
    finally:
        co.close()
