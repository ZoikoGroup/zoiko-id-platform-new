"""Requests for the contracted parts of a plan: recorded and visible, one open at a time, and refused where the plan does not include the service."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient
from onboarding_fixture import AUD, ISS, onboard
from zid_platform.main import create_app
from zid_platform.settings import Settings

BODY = {
    "service": "KEY_CUSTODY",
    "details": "We want to hold the signing key for our evidence in our own HSM.",
    "contact": "security@acme.example",
}


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


def test_a_request_is_recorded_listed_unique_while_open_and_withdrawable(rt_url: str) -> None:
    o = onboard(rt_url)
    h = o.bearer(o.owner)
    with TestClient(make_app(rt_url, o, "shadow")) as api:
        made = api.post("/v1/service-requests", json=BODY, headers=h)
        assert made.status_code == 201, made.text
        rid = made.json()["request_id"]
        assert made.json()["status"] == "REQUESTED"
        dup = api.post("/v1/service-requests", json=BODY, headers=h)
        assert dup.status_code == 409 and dup.json()["error"]["code"] == "SERVICE_REQUEST_OPEN"
        assert (
            api.post(
                "/v1/service-requests", json={**BODY, "service": "NAMED_SUPPORT"}, headers=h
            ).status_code
            == 201
        )
        assert (
            api.post(
                "/v1/service-requests", json={**BODY, "details": "short"}, headers=h
            ).status_code
            == 422
        )
        assert (
            api.post(
                "/v1/service-requests", json={**BODY, "service": "OTHER"}, headers=h
            ).status_code
            == 422
        )
        assert [
            i["service"] for i in api.get("/v1/service-requests", headers=h).json()["items"]
        ] == ["NAMED_SUPPORT", "KEY_CUSTODY"]
        assert api.post(f"/v1/service-requests/{rid}:withdraw", headers=h).status_code == 200
        assert api.post(f"/v1/service-requests/{rid}:withdraw", headers=h).status_code == 404
        assert (
            api.post("/v1/service-requests", json=BODY, headers=h).status_code == 201
        )  # a withdrawn one no longer blocks


def test_a_plan_without_the_service_cannot_ask_for_it(rt_url: str) -> None:
    o = onboard(rt_url)
    with TestClient(make_app(rt_url, o, "enforce")) as api:
        refused = api.post("/v1/service-requests", json=BODY, headers=o.bearer(o.owner))
        assert refused.status_code == 402, refused.text
        assert api.get("/v1/service-requests", headers=o.bearer(o.owner)).json()["items"] == []


def test_the_operator_plane_sees_every_companys_requests_and_moves_them_by_the_rules(
    rt_url: str,
) -> None:
    import asyncio
    import uuid

    from conftest import ctx, engine
    from zid_db import tenant_transaction
    from zid_platform.modules.commercial.ports import (
        ServiceRequestError,
        operator_scope,
        service_requests,
    )

    a, b = onboard(rt_url), onboard(rt_url)
    for o in (a, b):
        with TestClient(make_app(rt_url, o, "shadow")) as api:
            assert (
                api.post("/v1/service-requests", json=BODY, headers=o.bearer(o.owner)).status_code
                == 201
            )
    operator = str(uuid.uuid4())

    async def run() -> tuple[int, str, str, int]:
        async with engine(rt_url) as eng:
            async with tenant_transaction(eng, ctx(a.tenant)) as c, operator_scope(c):
                waiting = [
                    r
                    for r in await service_requests.open_requests(c)
                    if r["tenant_id"] in (a.tenant, b.tenant)
                ]
                first = waiting[0]["request_id"]
                with_note_missing = ""
                try:
                    await service_requests.move(
                        c, first, target="DECLINED", operator_id=operator, note=""
                    )
                except ServiceRequestError as e:
                    with_note_missing = e.code
                try:
                    await service_requests.move(
                        c, first, target="ACTIVE", operator_id=operator, note=""
                    )  # cannot skip IN_PROGRESS
                except ServiceRequestError as e:
                    skipped = e.code
                started = await service_requests.move(
                    c,
                    first,
                    target="IN_PROGRESS",
                    operator_id=operator,
                    note="We will contact you.",
                )
                done = await service_requests.move(
                    c, first, target="ACTIVE", operator_id=operator, note=""
                )
            async with tenant_transaction(eng, ctx(a.tenant)) as c:  # a company sees only its own
                mine = (
                    await c.execute(
                        __import__("sqlalchemy").text(
                            "SELECT count(*) FROM commercial.service_request"
                        )
                    )
                ).scalar_one()
            return (
                len(waiting),
                with_note_missing + "|" + skipped,
                started["status"] + done["status"],
                int(mine),
            )

    n, refused, moved, mine = asyncio.run(run())
    assert n == 2 and refused == "SERVICE_REQUEST_NOTE_REQUIRED|SERVICE_REQUEST_MOVE_REFUSED"
    assert moved == "IN_PROGRESSACTIVE" and mine == 1


def test_enforcement_can_be_switched_on_for_one_company_and_never_loosened(rt_url: str) -> None:
    import asyncio
    import uuid

    from conftest import ctx, engine
    from zid_db import tenant_transaction
    from zid_platform.modules.commercial.ports import enforcement_store, operator_scope

    piloting, other = onboard(rt_url), onboard(rt_url)
    operator = str(uuid.uuid4())

    async def set_mode(tenant: str, mode: str) -> dict:  # type: ignore[type-arg]
        async with (
            engine(rt_url) as eng,
            tenant_transaction(eng, ctx(tenant)) as c,
            operator_scope(c),
        ):
            await enforcement_store.write(
                c, tenant, mode=mode, reason="pilot company", actor=operator
            )
            return await enforcement_store.read(c, tenant, "shadow")

    state = asyncio.run(set_mode(piloting.tenant, "ENFORCE"))
    assert state["effective_mode"] == "enforce" and state["company_mode"] == "ENFORCE"

    # the deployment is in shadow: the pilot company is refused (402), the other still goes through
    with TestClient(make_app(rt_url, piloting, "shadow")) as api:
        assert (
            api.post(
                "/v1/service-requests", json=BODY, headers=piloting.bearer(piloting.owner)
            ).status_code
            == 402
        )
    with TestClient(make_app(rt_url, other, "shadow")) as api:
        assert (
            api.post(
                "/v1/service-requests", json=BODY, headers=other.bearer(other.owner)
            ).status_code
            == 201
        )

    # set back to shadow: it goes through again; and a company never sees another company's setting
    assert asyncio.run(set_mode(piloting.tenant, "SHADOW"))["effective_mode"] == "shadow"
    with TestClient(make_app(rt_url, piloting, "shadow")) as api:
        assert (
            api.post(
                "/v1/service-requests", json=BODY, headers=piloting.bearer(piloting.owner)
            ).status_code
            == 201
        )

    async def own_rows(tenant: str) -> int:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            return int(
                (
                    await c.execute(
                        __import__("sqlalchemy").text(
                            "SELECT count(*) FROM commercial.enforcement_policy"
                        )
                    )
                ).scalar_one()
            )

    assert asyncio.run(own_rows(other.tenant)) == 0
