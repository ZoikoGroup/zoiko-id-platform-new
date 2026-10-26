"""A company asks for a different plan; the commercial team answers (ZID-COM-001 §10; CR-032)."""

from __future__ import annotations

import asyncio
import uuid

import pytest
from conftest import ctx, engine
from fastapi.testclient import TestClient
from onboarding_fixture import AUD, ISS, onboard
from zid_db import tenant_transaction
from zid_platform.main import create_app
from zid_platform.modules.commercial.ports import PlanRequestError, operator_scope, plan_requests
from zid_platform.settings import Settings

RESOURCES = ["billable:*", "tenant:*"]


@pytest.fixture(scope="module")
def company(rt_url: str):  # type: ignore[no-untyped-def]
    o = onboard(rt_url, extra_resources=RESOURCES)
    app = create_app(
        Settings(
            database_url=rt_url,
            token_issuer=ISS,
            token_audience=AUD,
            public_base_url=AUD,
            environment="test",
            cell_id="uk-1",
        ),
        keyring=o.ring,
    )
    with TestClient(app) as api:
        yield o, api


def test_a_company_can_ask_for_a_plan_once_and_see_it(company) -> None:  # type: ignore[no-untyped-def]
    o, api = company
    h = o.bearer(o.owner)
    r = api.post(
        "/v1/billing/plan-requests",
        json={"plan": "BUSINESS", "note": "We need SSO for two customers."},
        headers=h,
    )
    assert r.status_code == 201, r.text
    rid = r.json()["request_id"]
    assert r.json()["status"] == "OPEN"

    # one open request at a time, and nothing about the entitlement moved
    again = api.post("/v1/billing/plan-requests", json={"plan": "SCALE"}, headers=h)
    assert again.status_code == 409 and again.json()["error"]["code"] == "PLAN_REQUEST_OPEN"
    ent = api.get(f"/v1/tenants/{o.tenant}/entitlements", headers=h).json()
    assert ent["source"] == "DEFAULT"

    mine = api.get("/v1/billing/plan-requests", headers=h).json()["items"]
    assert (
        [m["request_id"] for m in mine] == [rid]
        and mine[0]["status"] == "OPEN"
        and mine[0]["plan"] == "BUSINESS"
    )

    # withdrawing closes it and lets the company ask again
    w = api.post(f"/v1/billing/plan-requests/{rid}:withdraw", headers=h)
    assert w.status_code == 200 and w.json()["status"] == "WITHDRAWN"
    assert api.post(f"/v1/billing/plan-requests/{rid}:withdraw", headers=h).status_code == 409
    assert (
        api.post("/v1/billing/plan-requests", json={"plan": "LAUNCH"}, headers=h).status_code == 201
    )


def test_a_request_must_make_sense(company) -> None:  # type: ignore[no-untyped-def]
    o, api = company
    h = o.bearer(
        o.second
    )  # the other administrator; nothing is open for them to collide with the owner's
    # the plan the company is already on, and a plan that is not offered
    cur = api.get(f"/v1/tenants/{o.tenant}/entitlements", headers=h).json()["plan"]
    same = api.post("/v1/billing/plan-requests", json={"plan": cur}, headers=h)
    assert same.status_code in (409,) and same.json()["error"]["code"] in (
        "PLAN_ALREADY_CURRENT",
        "PLAN_REQUEST_OPEN",
    )
    nope = api.post("/v1/billing/plan-requests", json={"plan": "NOT_A_PLAN"}, headers=h)
    assert nope.status_code in (409, 422)


def test_another_company_never_sees_the_request_and_the_operator_answers_it(
    rt_url: str, company
) -> None:  # type: ignore[no-untyped-def]
    o, api = company
    other = onboard(rt_url, extra_resources=RESOURCES)
    other_app = create_app(
        Settings(
            database_url=rt_url,
            token_issuer=ISS,
            token_audience=AUD,
            public_base_url=AUD,
            environment="test",
            cell_id="uk-1",
        ),
        keyring=other.ring,
    )
    with TestClient(other_app) as other_api:
        assert (
            other_api.get("/v1/billing/plan-requests", headers=other.bearer(other.owner)).json()[
                "items"
            ]
            == []
        )

    open_id = next(
        m["request_id"]
        for m in api.get("/v1/billing/plan-requests", headers=o.bearer(o.owner)).json()["items"]
        if m["status"] == "OPEN"
    )
    operator = str(uuid.uuid4())

    async def operate() -> None:
        async with (
            engine(rt_url) as eng,
            tenant_transaction(eng, ctx(str(uuid.uuid4()))) as c,
            operator_scope(c),
        ):
            waiting = await plan_requests.open_requests(c)
            assert open_id in {w["request_id"] for w in waiting}
            assert {w["tenant_id"] for w in waiting} >= {o.tenant}
            done = await plan_requests.answer(
                c, open_id, decision="DECLINED", operator_id=operator, note="Pilot first."
            )
            assert done["status"] == "DECLINED"
            with pytest.raises(PlanRequestError, match="PLAN_REQUEST_CLOSED"):
                await plan_requests.answer(
                    c, open_id, decision="ACCEPTED", operator_id=operator, note=None
                )

    asyncio.run(operate())
    mine = api.get("/v1/billing/plan-requests", headers=o.bearer(o.owner)).json()["items"]
    answered = next(m for m in mine if m["request_id"] == open_id)
    assert answered["status"] == "DECLINED" and answered["decision_note"] == "Pilot first."

    # outside operator scope the company-scoped table shows an operator nothing of another company
    async def outside() -> int:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(str(uuid.uuid4()))) as c:
            return len(await plan_requests.my_requests(c))

    assert asyncio.run(outside()) == 0
