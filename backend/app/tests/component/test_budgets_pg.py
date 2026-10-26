"""Bill preview, budgets and included-quota warnings (ZID-COM-001 §7; CR-029).

The preview rates month-to-date usage with the invoice's own arithmetic; each threshold notifies once a month;
a budget can never throttle or refuse anything.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from conftest import CELL, ctx, engine
from fastapi.testclient import TestClient
from onboarding_fixture import AUD, ISS, onboard
from sqlalchemy import text
from zid_db import tenant_transaction
from zid_platform.main import create_app
from zid_platform.modules.commercial.application import budget_service, recorder, service
from zid_platform.modules.commercial.domain import billable as b
from zid_platform.modules.commercial.domain.budgets import BudgetError
from zid_platform.modules.commercial.infrastructure import catalogue_store
from zid_platform.settings import Settings

SEED_BOOK = "00000000-0000-4000-8000-0000000000b1"
NOW = datetime.now(UTC)
MONTH = b.window_month(NOW)
M_START = datetime(MONTH.year, MONTH.month, 1, tzinfo=UTC)
AT = M_START + timedelta(days=10)  # evaluation time: ten days into this month


def uid() -> str:
    return str(uuid.uuid4())


async def business_tenant(rt_url: str, tenant: str, mah: int) -> None:
    async with engine(rt_url) as eng:
        async with eng.begin() as c:
            draft = await catalogue_store.create_draft(
                c, copy_of=SEED_BOOK, created_by=uid(), note="budgets"
            )
            await catalogue_store.approve(
                c,
                draft["price_book_id"],
                approver=uid(),
                effective_from=M_START - timedelta(days=40),
                now=datetime.now(UTC),
            )
        async with tenant_transaction(eng, ctx(tenant)) as c:
            await catalogue_store.set_entitlement(
                c,
                tenant_id=tenant,
                plan_code="BUSINESS",
                price_book_id=draft["price_book_id"],
                actor=None,
                now=M_START - timedelta(days=35),
            )
            await recorder.record(
                c,
                tenant_id=tenant,
                meter="MAH",
                key=f"mah:{tenant}",
                at=M_START + timedelta(days=2),
                quantity=mah,
            )


def test_preview_budgets_and_quota_warnings_notify_once_and_never_throttle(
    rt_url: str, owner_url: str
) -> None:
    tenant, other, actor = uid(), uid(), uid()

    async def run() -> None:
        await business_tenant(rt_url, tenant, 30_000)
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            p = await budget_service.preview(c, connections={"saml_sso": 1}, now=AT)
            assert p["status"] == "ESTIMATE" and p["usage_to_date"] == {"MAH": "30000"}
            assert p["bill_to_date"] == "1499"  # 30,000 is inside Business's 50,000
            assert p["projected_bill"] is not None and Decimal(p["projected_bill"]) >= Decimal(
                p["bill_to_date"]
            )
            assert p["projection_confidence"] == "NORMAL" and "An estimate" in p["assumptions"][0]
            early = await budget_service.preview(
                c, connections={}, now=M_START + timedelta(hours=2)
            )
            assert early["projected_bill"] is None and early["projection_confidence"] == "NONE"

            for kw, code in (
                ({"meter": "MAH", "currency": "USD"}, "BUDGET_TARGET_INVALID"),
                ({"meter": None, "currency": None}, "BUDGET_TARGET_INVALID"),
                ({"meter": "LOGINS", "currency": None}, "METER_UNKNOWN"),
            ):
                with pytest.raises(BudgetError) as e:
                    await budget_service.set_budget(
                        c,
                        tenant_id=tenant,
                        monthly_limit=Decimal(10),
                        thresholds=[50],
                        actor=actor,
                        **kw,  # type: ignore[arg-type]
                    )
                assert e.value.code == code
            await budget_service.set_budget(
                c,
                tenant_id=tenant,
                meter=None,
                monthly_limit=Decimal(1600),
                currency="USD",
                thresholds=[50, 80, 100],
                actor=actor,
            )
            await budget_service.set_budget(
                c,
                tenant_id=tenant,
                meter="MAH",
                monthly_limit=Decimal(40_000),
                currency=None,
                thresholds=[50, 80, 100],
                actor=actor,
            )
            out = await budget_service.evaluate(
                c, tenant_id=tenant, connections={"saml_sso": 1}, now=AT
            )
            got = sorted((r["key"], r["threshold"]) for r in out["raised"])
            assert got == [
                ("BUDGET:BILL", 50),
                ("BUDGET:BILL", 80),  # 1,499 of 1,600 is 93.7%
                ("BUDGET:MAH", 50),  # 30,000 of 40,000 is 75%
                ("QUOTA:MAH", 50),  # 30,000 of the 50,000 included is 60%
            ]
            again = await budget_service.evaluate(
                c, tenant_id=tenant, connections={"saml_sso": 1}, now=AT
            )
            assert again["raised"] == []  # each threshold notifies once a month

            # a budget at or over its limit throttles nothing: the paid-extra gate is unaffected
            await budget_service.set_budget(
                c,
                tenant_id=tenant,
                meter=None,
                monthly_limit=Decimal(1),
                currency="USD",
                thresholds=[100],
                actor=actor,
            )
            await budget_service.evaluate(c, tenant_id=tenant, connections={}, now=AT)
            assert (await service.entitled(c, "saml_sso", used=0, enforcement="enforce")).allowed
            assert (
                len(await budget_service.budgets(c)) == 2
            )  # the replaced bill budget is inactive, kept
            events = (
                await c.execute(
                    text(
                        "SELECT count(*) FROM events.outbox_event WHERE event_type = "
                        "'com.zoiko.id.commercial.budget.threshold_crossed.v1'"
                    )
                )
            ).scalar_one()
            assert events == 5

        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(other)) as c:
            assert await budget_service.budgets(c) == []

    asyncio.run(run())


def test_preview_and_budgets_over_http(rt_url: str) -> None:
    o = onboard(
        rt_url,
        extra_actions=["commercial.read", "commercial.budget.manage"],
        extra_resources=["billable:*", "budget:*"],
    )
    asyncio.run(business_tenant(rt_url, o.tenant, 1000))
    app = create_app(
        Settings(
            database_url=rt_url,
            token_issuer=ISS,
            token_audience=AUD,
            public_base_url=AUD,
            environment="test",
            cell_id="uk-1",
            cell_uuid=CELL,
        ),
        keyring=o.ring,
    )
    adm = o.bearer(o.owner)
    with TestClient(app, base_url=ISS) as http:
        p = http.get("/v1/billing/preview", headers=adm)
        assert p.status_code == 200, p.text
        assert not any(
            k.startswith("_") for k in p.json()
        )  # internal figures never leave the server
        r = http.put("/v1/budgets", json={"meter": "MAH", "monthly_limit": "1000"}, headers=adm)
        assert r.status_code == 200, r.text
        assert (
            http.put(
                "/v1/budgets",
                json={"meter": "MAH", "monthly_limit": "1", "thresholds": [0]},
                headers=adm,
            ).status_code
            == 422
        )
        items = http.get("/v1/budgets", headers=adm).json()["items"]
        assert [i["meter"] for i in items] == ["MAH"]
        r = http.post("/v1/budgets:evaluate", headers=adm)
        assert r.status_code == 200 and r.json()["status"] in ("EVALUATED", "UNPRICED"), r.text
        assert http.get("/v1/budgets").status_code == 401


def test_public_estimate_reproduces_the_worked_bill_and_never_quotes(
    rt_url: str, owner_url: str
) -> None:
    """The pricing calculator runs the invoice's own rating over the approved book (ZID-COM-001 §13, §19)."""
    tenant = uid()

    async def run() -> None:
        await business_tenant(rt_url, tenant, 1)
        async with engine(rt_url) as eng, eng.begin() as c:
            out = await service.estimate(
                c,
                plan_code="BUSINESS",
                usage={
                    "MAH": Decimal(68_000),
                    "AUTH_EVENT": Decimal(1_200_000),
                    "POLICY_DECISION": Decimal(6_500_000),
                    "M2M_GRANT": Decimal(300_000),
                },
                connections={"saml_sso": 2},
            )
            assert out["status"] == "ESTIMATE" and out["monthly_estimate"] == "2053"
            assert "not a quotation" in out["assumptions"][0]
            with pytest.raises(Exception, match="No such plan"):
                await service.estimate(c, plan_code="NOPE", usage={}, connections={})

    asyncio.run(run())
