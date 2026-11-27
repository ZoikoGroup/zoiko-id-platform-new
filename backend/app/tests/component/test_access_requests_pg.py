"""A person can ask for a permission or a limit; only they see their request, and only an administrator can answer it.

Asking grants nothing. Nobody answers their own request, and a Member cannot read the company's list.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from admin_fixture import make_admin
from conftest import ctx, engine
from fastapi.testclient import TestClient
from onboarding_fixture import AUD, ISS, _session, onboard
from zid_db import tenant_transaction
from zid_platform.main import create_app
from zid_platform.modules.principal.domain.principal import (
    Principal,
    PrincipalStatus,
    PrincipalType,
)
from zid_platform.modules.principal.infrastructure.principal_store import create_principal
from zid_platform.modules.session.ports import issue_access_token
from zid_platform.settings import Settings


def _person(rt_url: str, o: Any, actions: list[str] | None) -> str:
    async def run() -> str:
        p = str(uuid.uuid4())
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(o.tenant)) as c:
            await create_principal(
                c, Principal(p, o.tenant, PrincipalType.HUMAN, PrincipalStatus.ACTIVE, "uk-1")
            )
            if actions:
                await make_admin(c, o.tenant, p, actions, ["*"], release=False)
            o.sessions[p] = await _session(c, o.tenant, p)
        return p

    return asyncio.run(run())


def _bearer(o: Any, p: str) -> dict[str, str]:
    now = datetime.now(UTC)
    tok = issue_access_token(
        o.ring, issuer=ISS, audience=AUD, subject=p, tenant_id=o.tenant, client_id="console", now=now,
        session_id=o.sessions[p], auth_time=now - timedelta(seconds=5), scope=frozenset(),
    )  # fmt: skip
    return {"Authorization": f"Bearer {tok}"}


def _client(rt_url: str, o: Any) -> TestClient:
    app = create_app(
        Settings(database_url=rt_url, token_issuer=ISS, token_audience=AUD, public_base_url=AUD, environment="test", cell_id="uk-1"),
        keyring=o.ring,
    )  # fmt: skip
    return TestClient(app, base_url=ISS)


ASK = {
    "actions": ["invoice.approve"],
    "value_amount": "500",
    "value_currency": "GBP",
    "reason": "I approve the weekly supplier invoices.",
}


def test_a_person_asks_and_sees_only_their_own_requests(rt_url: str) -> None:
    o = onboard(rt_url)
    asha = _person(rt_url, o, None)
    ravi = _person(rt_url, o, None)
    with _client(rt_url, o) as api:
        made = api.post("/v1/access-requests", json=ASK, headers=_bearer(o, asha))
        assert made.status_code == 201, made.text
        assert made.json()["status"] == "OPEN" and "requester_principal_id" not in made.json()
        assert (
            len(api.get("/v1/access-requests/mine", headers=_bearer(o, asha)).json()["items"]) == 1
        )
        assert (
            api.get("/v1/access-requests/mine", headers=_bearer(o, ravi)).json()["items"] == []
        )  # never another person's


def test_a_member_cannot_read_the_companys_list_or_answer_a_request(rt_url: str) -> None:
    o = onboard(rt_url)
    asha = _person(rt_url, o, None)
    ravi = _person(rt_url, o, None)
    with _client(rt_url, o) as api:
        rid = api.post("/v1/access-requests", json=ASK, headers=_bearer(o, asha)).json()[
            "request_id"
        ]
        assert api.get("/v1/access-requests", headers=_bearer(o, ravi)).status_code == 403
        assert (
            api.post(
                f"/v1/access-requests/{rid}:fulfil", json={}, headers=_bearer(o, ravi)
            ).status_code
            == 403
        )
        assert (
            api.post(
                f"/v1/access-requests/{rid}:decline", json={}, headers=_bearer(o, ravi)
            ).status_code
            == 403
        )


def test_an_administrator_answers_and_the_person_sees_the_answer(rt_url: str) -> None:
    o = onboard(rt_url)
    asha = _person(rt_url, o, None)
    with _client(rt_url, o) as api:
        rid = api.post("/v1/access-requests", json=ASK, headers=_bearer(o, asha)).json()[
            "request_id"
        ]
        listed = api.get("/v1/access-requests", headers=o.bearer(o.owner, scope=""))
        assert listed.status_code == 200, listed.text
        assert [(i["request_id"], i["requester_principal_id"]) for i in listed.json()["items"]] == [
            (rid, asha)
        ]
        no = api.post(
            f"/v1/access-requests/{rid}:decline",
            json={"note": "Ask your manager first."},
            headers=o.bearer(o.owner, scope=""),
        )
        assert no.status_code == 200 and no.json()["status"] == "DECLINED", no.text
        again = api.post(
            f"/v1/access-requests/{rid}:fulfil", json={}, headers=o.bearer(o.owner, scope="")
        )
        assert again.status_code == 409  # already answered
        mine = api.get("/v1/access-requests/mine", headers=_bearer(o, asha)).json()["items"][0]
        assert mine["status"] == "DECLINED" and mine["decision_note"] == "Ask your manager first."


def test_nobody_answers_their_own_request(rt_url: str) -> None:
    o = onboard(rt_url)
    with _client(rt_url, o) as api:
        rid = api.post("/v1/access-requests", json=ASK, headers=o.bearer(o.owner, scope="")).json()[
            "request_id"
        ]
        own = api.post(
            f"/v1/access-requests/{rid}:fulfil", json={}, headers=o.bearer(o.owner, scope="")
        )
        assert own.status_code == 403 and own.json()["error"]["code"] == "ACCESS_REQUEST_OWN"


def test_withdrawing_is_only_for_the_person_who_asked_and_the_number_waiting_is_capped(
    rt_url: str,
) -> None:
    o = onboard(rt_url)
    asha = _person(rt_url, o, None)
    ravi = _person(rt_url, o, None)
    with _client(rt_url, o) as api:
        rid = api.post("/v1/access-requests", json=ASK, headers=_bearer(o, asha)).json()[
            "request_id"
        ]
        assert (
            api.post(f"/v1/access-requests/{rid}:withdraw", headers=_bearer(o, ravi)).status_code
            == 404
        )
        assert (
            api.post(f"/v1/access-requests/{rid}:withdraw", headers=_bearer(o, asha)).json()[
                "status"
            ]
            == "WITHDRAWN"
        )
        for _ in range(5):
            assert (
                api.post("/v1/access-requests", json=ASK, headers=_bearer(o, asha)).status_code
                == 201
            )
        assert (
            api.post("/v1/access-requests", json=ASK, headers=_bearer(o, asha)).status_code == 409
        )
        bad = api.post(
            "/v1/access-requests", json={**ASK, "value_currency": None}, headers=_bearer(o, ravi)
        )
        assert bad.status_code == 422  # an amount needs its currency
