"""The profitability report on Postgres (ZID-COM-WF-2026-11 CW-13): revenue from computed rating runs, provider cost through the vendor's SKU, margin in one
currency, an inferred cost flagged, and the operator read policy that lets the report see every company while a company still sees only its own."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from conftest import ctx, engine
from sqlalchemy import text
from zid_db import tenant_transaction
from zid_platform.modules.commercial.ports import profitability_service as svc


def uid() -> uuid.UUID:
    return uuid.uuid4()


async def rated(
    c,
    tenant: uuid.UUID,
    month: date,
    sku: str,
    net: str,
    currency: str = "EUR",
    state: str = "COMPUTED",
    attempt: int = 1,
) -> None:
    run = uid()
    await c.execute(
        text(
            "INSERT INTO commercial.rating_run (run_id, tenant_id, period, attempt, state, currency, subtotal, credit_balance, inputs_hash, outputs_hash) "
            "VALUES (:r, :t, :p, :at, :s, :c, :n, 0, :h, :h)"
        ),
        {
            "r": run,
            "t": tenant,
            "p": month,
            "s": state,
            "c": currency,
            "n": net,
            "h": "a" * 64,
            "at": attempt,
        },
    )
    await c.execute(
        text(
            "INSERT INTO commercial.rated_usage (line_id, run_id, tenant_id, period, seq, kind, sku, segment, formula, net, currency) "
            "VALUES (:l, :r, :t, :p, 1, 'FEE', :k, 0, 'test', :n, :c)"
        ),
        {"l": uid(), "r": run, "t": tenant, "p": month, "k": sku, "n": net, "c": currency},
    )


def test_margin_run_rate_and_retention_from_real_rows(rt_url: str, owner_url: str) -> None:
    async def run() -> None:
        a, b, c3, d = uid(), uid(), uid(), uid()
        sku = f"S{uuid.uuid4().hex[:8].upper()}"
        async with engine(owner_url) as eng, eng.begin() as c:
            base = (await c.execute(text("SELECT sku_id FROM commercial.sku LIMIT 1"))).scalar_one()
            for m, ta, tb in (
                (date(2026, 8, 1), "100", "100"),
                (date(2026, 9, 1), "100", "100"),
                (date(2026, 10, 1), "100", "0"),
            ):
                await rated(c, a, m, sku, ta)
                if tb != "0":
                    await rated(c, b, m, sku, tb)
            await rated(c, c3, date(2026, 10, 1), sku, "40")
            await rated(
                c, c3, date(2026, 10, 1), sku, "999", state="SUPERSEDED", attempt=2
            )  # a superseded run never counts
            await rated(
                c, d, date(2026, 10, 1), sku, "70", currency="USD"
            )  # another currency is its own line, never added
            vendor = f"v{uuid.uuid4().hex[:8]}"
            await c.execute(
                text(
                    "INSERT INTO commercial.vendor (vendor_id, display_name, legal_role, sku_id, created_by) VALUES (:v, 'Test vendor', 'SUBPROCESSOR', :s, :b)"
                ),
                {"v": vendor, "s": base, "b": uid()},
            )
            rate = uid()
            await c.execute(
                text(
                    "INSERT INTO commercial.vendor_rate (rate_id, vendor_id, check_type, country, base_price, retry_price, currency, confidence, source_ref, recorded_by) VALUES (:r, :v, 'ID', 'DE', 10, 5, 'EUR', 'ESTIMATE', 'quote', :b)"
                ),
                {"r": rate, "v": vendor, "b": uid()},
            )
            await c.execute(
                text(
                    "INSERT INTO commercial.vendor_cost_event (cost_id, tenant_id, vendor_id, supplier_ref, check_type, country, outcome, attempt_no, cost, cost_currency, rate_id, chargeable, charge_basis, occurred_at) "
                    "VALUES (:i, :t, :v, 's1', 'ID', 'DE', 'COMPLETED', 1, 30, 'EUR', :r, true, 'BASE', '2026-10-05T10:00:00Z')"
                ),
                {"i": uid(), "t": a, "v": vendor, "r": rate},
            )
            cost_sku = base

        async with engine(rt_url) as eng:
            async with tenant_transaction(eng, ctx(str(uid()))) as c:
                out = await svc.report(
                    c, start=date(2026, 8, 1), end=date(2026, 10, 1), as_of=date(2026, 11, 13)
                )
            # a company sees only its own rated lines, however the report sees all
            async with tenant_transaction(eng, ctx(str(a))) as c:
                seen = (
                    await c.execute(
                        text("SELECT count(DISTINCT tenant_id) FROM commercial.rated_usage")
                    )
                ).scalar_one()
                assert seen == 1

        october = next(m for m in out["margins"] if m["month"] == "2026-10")
        eur = next(x for x in october["lines"] if x["sku"] == sku and x["currency"] == "EUR")
        assert eur["revenue"] == "140.00", (
            "company A 100 + company C 40; the superseded run is left out"
        )
        assert eur["cost"] is None, "the cost sits on the vendor's own SKU, not on this one"
        usd = next(x for x in october["lines"] if x["sku"] == sku and x["currency"] == "USD")
        assert usd["revenue"] == "70.00"
        assert out["margins"][0]["month"] == "2026-08"
        assert any(
            u["sku"] == cost_sku and u["inferred"] is True and u["cost"] == "30.00"
            for u in october["cost_without_revenue_in_that_currency"]
        ) or any(x["sku"] == cost_sku and x["cost_is_inferred"] is True for x in october["lines"])

        assert (
            out["run_rate"]["EUR"]["months_used"] == 3
            and out["run_rate"]["EUR"]["complete_window"] is True
        )
        assert out["run_rate"]["EUR"]["monthly_average"] == "180.00"
        ret = out["retention"]
        assert ret["between"] == ["2026-09", "2026-10"]
        eur_ret = ret["by_currency"]["EUR"]
        assert eur_ret["churned"] == {"companies": 1, "revenue": "100.00"}
        assert eur_ret["new"] == {"companies": 1, "revenue": "40.00"}
        assert eur_ret["gross_retention_pct"] == "50.00"

    asyncio.run(run())


def test_fx_rate_set_lifecycle_and_consolidation(rt_url: str, owner_url: str) -> None:
    from datetime import timedelta

    import pytest
    from sqlalchemy.exc import DBAPIError
    from zid_platform.modules.commercial.ports import fx_store

    async def run() -> None:
        author, other = str(uid()), str(uid())
        tenant_a, tenant_b = uid(), uid()
        async with engine(owner_url) as eng, eng.begin() as c:
            await rated(c, tenant_a, date(2026, 9, 1), "S1", "100", currency="EUR")
            await rated(c, tenant_b, date(2026, 9, 1), "S1", "50", currency="JPY")
        async with engine(rt_url) as eng, eng.begin() as c:
            # no active set: nothing but dollars is added, and the report says what it could not convert
            base_set, _ = await fx_store.active(c)
            made = await fx_store.create_draft(
                c,
                source_ref="ECB reference rates 2026-09-30",
                as_of=date(2026, 9, 30),
                created_by=author,
            )
            sid = made["set_id"]
            with pytest.raises(Exception) as e:
                await fx_store.activate(c, sid, activator=other, now=datetime.now(UTC))
            assert (
                "no rates" in str(e.value.__cause__ or e.value)
                or getattr(e.value, "code", "") == "FX_ACTIVATION_REFUSED"
            )
            await fx_store.put_rate(c, sid, "EUR", "1.0850")
            with pytest.raises(Exception) as e2:
                await fx_store.activate(c, sid, activator=author, now=datetime.now(UTC))
            assert getattr(e2.value, "code", "") == "FX_SELF_ACTIVATION"
            await fx_store.activate(c, sid, activator=other, now=datetime.now(UTC))
            for call in (
                lambda: fx_store.put_rate(c, sid, "JPY", "0.0067"),
                lambda: fx_store.withdraw(c, sid),
            ):
                with pytest.raises(Exception) as e3:
                    await call()
                assert getattr(e3.value, "code", "") in {"FX_SET_NOT_DRAFT"}
            active_set, rates = await fx_store.active(c)
            assert (
                active_set is not None
                and active_set["version"] >= 1
                and rates == {"EUR": Decimal("1.085000000000")}
            )
            assert base_set is None or base_set["version"] < active_set["version"]
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(str(uid()))) as c:
            out = await svc.report(
                c, start=date(2026, 9, 1), end=date(2026, 9, 1), as_of=date(2026, 11, 13)
            )
        sep = next(m for m in out["consolidated_usd"] if m["month"] == "2026-09")
        assert out["fx"]["source_ref"] == "ECB reference rates 2026-09-30"
        assert sep["revenue"]["rates_used"] == [
            {"currency": "EUR", "usd_per_unit": "1.085000000000"}
        ]
        assert sep["revenue"]["not_converted"] == ["JPY"] and sep["revenue"]["complete"] is False
        assert sep["margin_usd"] is None, (
            "a margin is only stated when every currency could be converted"
        )
        # the database refuses to change an active set or its rates, and never deletes it
        async with engine(owner_url) as eng:
            for sql in (
                "UPDATE commercial.fx_rate SET usd_per_unit = 2",
                "DELETE FROM commercial.fx_rate",
                "UPDATE commercial.fx_rate_set SET source_ref = 'edited' WHERE status = 'ACTIVE'",
                "DELETE FROM commercial.fx_rate_set WHERE status = 'ACTIVE'",
            ):
                with pytest.raises(DBAPIError):
                    async with eng.begin() as c:
                        await c.execute(text(sql))
        _ = timedelta

    asyncio.run(run())
