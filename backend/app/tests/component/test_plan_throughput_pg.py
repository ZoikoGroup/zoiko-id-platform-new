"""A company's request allowance follows its plan, end to end: the same base limit gives a Developer company 3 requests a minute and a Business company
15 (x5), the usage screen shows the plan's number, and a plan with no limit (Enterprise, contracted) is never refused."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from conftest import engine
from fastapi.testclient import TestClient
from onboarding_fixture import AUD, ISS, onboard
from sqlalchemy import text
from zid_platform.main import create_app
from zid_platform.settings import Settings

BASE = 3


def app_for(rt_url: str, o: Any) -> Any:
    return create_app(
        Settings(database_url=rt_url, token_issuer=ISS, token_audience=AUD, public_base_url=AUD, environment="test", cell_id="uk-1", api_rate_limit_per_minute=BASE),
        keyring=o.ring,
    )


def on_plan(owner_url: str, tenant: str, plan: str) -> None:
    async def put() -> None:
        async with engine(owner_url) as eng, eng.begin() as c:
            await c.execute(
                text("INSERT INTO commercial.entitlement (entitlement_id, tenant_id, plan_code, state, effective_from, source) VALUES (:i, :t, :p, 'ACTIVE', now(), 'OPERATOR')"),
                {"i": uuid.uuid4(), "t": uuid.UUID(tenant), "p": plan},
            )

    asyncio.run(put())


def calls_until_refused(api: TestClient, headers: dict[str, str], cap: int) -> int:
    for n in range(1, cap + 1):
        if api.get("/v1/tenant", headers=headers).status_code == 429:
            return n - 1
    return cap


def test_the_request_allowance_follows_the_plan(rt_url: str, owner_url: str) -> None:
    dev, biz, ent = onboard(rt_url), onboard(rt_url), onboard(rt_url)
    on_plan(owner_url, biz.tenant, "BUSINESS")
    on_plan(owner_url, ent.tenant, "ENTERPRISE")
    for o, expected in ((dev, BASE), (biz, BASE * 5)):
        with TestClient(app_for(rt_url, o), base_url=ISS) as api:
            h = o.bearer(o.owner)
            assert calls_until_refused(api, h, 60) == expected  # a Developer company: the base; a Business company: five times it
    with TestClient(app_for(rt_url, ent), base_url=ISS) as api:
        assert calls_until_refused(api, ent.bearer(ent.owner), 40) == 40  # contracted: no request limit


def test_the_usage_screen_shows_the_plans_number(rt_url: str, owner_url: str) -> None:
    biz = onboard(rt_url, extra_actions=["metering.read"], extra_resources=["usage:*"])
    on_plan(owner_url, biz.tenant, "SCALE")
    with TestClient(app_for(rt_url, biz), base_url=ISS) as api:
        r = api.get("/v1/usage/limits", headers=biz.bearer(biz.owner))
        assert r.status_code == 200, r.text
        assert r.json()["enforced"] is True and r.json()["limit_per_minute"] == BASE * 20
