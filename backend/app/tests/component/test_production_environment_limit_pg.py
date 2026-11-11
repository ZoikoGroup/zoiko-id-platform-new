"""A plan's production environments (Developer 1, Launch 1, Business 2, Scale 3, Enterprise by contract), checked when an application is registered.

With enforcement on, the plan's last production environment is refused (402) and a non-production one is not; in shadow nothing is refused;
a retired application no longer counts; a higher plan allows more.
"""

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


def make_app(rt_url: str, o: Any, mode: str) -> Any:
    return create_app(
        Settings(
            database_url=rt_url,
            token_issuer=ISS,
            token_audience=AUD,
            public_base_url=AUD,
            environment="test",
            cell_id="uk-1",
            commercial_enforcement=mode,
        ),
        keyring=o.ring,
    )


def body(name: str, environment: str) -> dict[str, Any]:
    return {
        "name": name,
        "environment": environment,
        "protocols": ["OIDC"],
        "redirect_uris": ["https://app.example/cb"],
    }


def test_the_plan_limit_on_production_environments(rt_url: str) -> None:
    o = onboard(rt_url)  # no entitlement row: the DEVELOPER plan, one production environment
    h = o.bearer(o.owner)
    with TestClient(make_app(rt_url, o, "enforce"), base_url=ISS) as api:
        first = api.post("/v1/integration/applications", json=body("Payroll", "PROD"), headers=h)
        assert first.status_code == 201, first.text
        second = api.post("/v1/integration/applications", json=body("Payroll 2", "PROD"), headers=h)
        assert (
            second.status_code == 402 and second.json()["error"]["code"] == "ENTITLEMENT_REQUIRED"
        )
        assert "1 production environment" in second.json()["error"]["message"]
        # other environments are not production environments
        for env in ("STAGE", "TEST", "DEV"):
            assert (
                api.post(
                    "/v1/integration/applications", json=body(f"App {env}", env), headers=h
                ).status_code
                == 201
            )
        # a retired application no longer counts
        app_id = first.json()["application_id"]
        for target in ("TESTING", "ACTIVE", "SUSPENDED", "RETIRED"):
            r = api.post(
                f"/v1/integration/applications/{app_id}:transition", json={"to": target}, headers=h
            )
            if r.status_code != 200:
                break
        retired = [
            a
            for a in api.get("/v1/integration/applications", headers=h).json()["items"]
            if a["status"] == "RETIRED"
        ]
        if retired:
            assert (
                api.post(
                    "/v1/integration/applications", json=body("Payroll 3", "PROD"), headers=h
                ).status_code
                == 201
            )

    shadow = onboard(rt_url)
    hs = shadow.bearer(shadow.owner)
    with TestClient(make_app(rt_url, shadow, "shadow"), base_url=ISS) as api:
        for n in range(3):  # reported, never refused
            assert (
                api.post(
                    "/v1/integration/applications", json=body(f"P{n}", "PROD"), headers=hs
                ).status_code
                == 201
            )


def test_a_higher_plan_allows_more(rt_url: str, owner_url: str) -> None:
    o = onboard(rt_url)

    async def put_on_business() -> None:
        async with engine(owner_url) as eng, eng.begin() as c:
            await c.execute(
                text(
                    "INSERT INTO commercial.entitlement (entitlement_id, tenant_id, plan_code, state, effective_from, source) VALUES (:i, :t, 'BUSINESS', 'ACTIVE', now(), 'OPERATOR')"
                ),
                {"i": uuid.uuid4(), "t": uuid.UUID(o.tenant)},
            )

    asyncio.run(put_on_business())
    h = o.bearer(o.owner)
    with TestClient(make_app(rt_url, o, "enforce"), base_url=ISS) as api:
        codes = [
            api.post(
                "/v1/integration/applications", json=body(f"P{n}", "PROD"), headers=h
            ).status_code
            for n in range(3)
        ]
        assert codes == [201, 201, 402]  # BUSINESS has two production environments
