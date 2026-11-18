"""EDD information requests and the owners' roll-up over HTTP (WF06 section 10 and 12; CR-122), on PostgreSQL: a reviewer asks, the customer answers in words that never say why, someone else reviews, and a company's owners roll up
without telling the company what a check found."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any

from conftest import CELL, ctx, engine
from fastapi.testclient import TestClient
from onboarding_fixture import AUD, ISS, onboard
from test_screening_cases_api_pg import Q, _app, _audit, _grant, _people, _seed
from test_screening_requests_api_pg import BASE, _case, _load_lists, pack
from zid_db import tenant_transaction
from zid_platform.main import create_app
from zid_platform.modules.screening.application import edd
from zid_platform.settings import Settings

ITEMS = {"items": ["SOURCE_OF_FUNDS", "SUPPORTING_DOCUMENT"], "due_in_days": 20}


def _open_edd(rt_url: str, tenant: str, case: str, by: str) -> str:
    async def run() -> str:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            return (
                await edd.open_request(
                    c,
                    tenant,
                    case,
                    ITEMS["items"],
                    requested_by=by,
                    due_in_days=20,
                    now=datetime.now(UTC),
                )
            )["edd_id"]

    return asyncio.run(run())


def _reply(rt_url: str, tenant: str, case: str, provided: list[str]) -> None:
    async def run() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            await edd.receive_for_case(c, case, provided, now=datetime.now(UTC))

    asyncio.run(run())


def test_a_customer_sees_what_more_is_wanted_answers_it_and_a_companys_owners_roll_up_without_a_finding(
    rt_url: str, owner_url: str
) -> None:
    o = onboard(rt_url)
    _load_lists(rt_url)
    clear_subject, hit_subject, ghost = (str(uuid.uuid4()) for _ in range(3))
    from datetime import timedelta

    async def lookup(
        conn: Any, tenant: str, subject_type: Any, subject_ref: str, now: datetime
    ) -> Any:
        if subject_ref in (clear_subject, hit_subject):
            return ("IDENTITY_VERIFIED_FOR_SCOPE", datetime.now(UTC) - timedelta(days=2))
        return None

    app = create_app(
        Settings(database_url=rt_url, token_issuer=ISS, token_audience=AUD, public_base_url=AUD, environment="test", cell_id="uk-1", cell_uuid=CELL),
        keyring=o.ring,
    )  # fmt: skip
    app.state.screening_market_packs = pack(o.tenant)
    app.state.screening_identity_lookup = lookup
    app.state.screening_enforce_gates = False
    with TestClient(app, base_url=ISS) as http:
        owner = o.bearer(o.owner)
        clear = http.post(
            f"{BASE}/cases",
            json=_case(clear_subject),
            headers={**owner, "Idempotency-Key": "edd-api-clear-1"},
        ).json()
        hit = http.post(
            f"{BASE}/cases",
            json=_case(hit_subject, "Eric Badege"),
            headers={**owner, "Idempotency-Key": "edd-api-hit-001"},
        ).json()

        # nothing is wanted yet
        none = http.get(f"{BASE}/cases/{clear['request_id']}/information", headers=owner).json()
        assert none["open"] is False and none["request"] is None
        assert (
            http.post(
                f"{BASE}/cases/{clear['request_id']}/information",
                json={"provided": ["SOURCE_OF_FUNDS"]},
                headers=owner,
            ).json()["error"]["code"]
            == "REQUEST_NOT_FOUND"
        )
        assert http.get(f"{BASE}/cases/{hit['request_id']}/information").status_code in (401, 403)

        # a reviewer asked for two things; the customer is told only what, never why
        _open_edd(rt_url, o.tenant, hit["request_id"], str(uuid.uuid4()))
        asked = http.get(f"{BASE}/cases/{hit['request_id']}/information", headers=owner).json()
        assert asked["open"] is True and asked["request"]["still_needed"] == ITEMS["items"]
        for word in ("sanction", "pep", "politically", "media", "screening", "candidate", "risk"):
            assert word not in asked["request"]["message"].lower(), word

        part = http.post(
            f"{BASE}/cases/{hit['request_id']}/information",
            json={"provided": ["SOURCE_OF_FUNDS"]},
            headers=owner,
        )
        assert part.status_code == 202 and part.json()["request"]["still_needed"] == [
            "SUPPORTING_DOCUMENT"
        ]
        full = http.post(
            f"{BASE}/cases/{hit['request_id']}/information",
            json={"provided": ["SUPPORTING_DOCUMENT"]},
            headers=owner,
        )
        assert full.json()["request"]["state"] == "RECEIVED"
        assert (
            http.post(
                f"{BASE}/cases/{hit['request_id']}/information",
                json={"provided": ["bank password"]},
                headers=owner,
            ).status_code
            == 422
        )

        # the owners: one clear, one a possible match waiting for a person, one never screened
        body = {
            "owners": [
                {"person_ref": clear_subject, "status": "CONFIRMED"},
                {"person_ref": hit_subject, "status": "NEEDS_CONFIRMATION"},
                {"person_ref": ghost, "status": "NEEDS_CONFIRMATION"},
            ]
        }
        out = http.post(f"{BASE}/ownership/rollup", json=body, headers=owner)
        assert out.status_code == 200, out.text
        data = out.json()
        assert data["status"] == "IN_REVIEW" and data["needs_case"] == [ghost]
        reasons = {v["person_ref"]: v["reason"] for v in data["owners"]}
        assert reasons == {
            clear_subject: "OWNER_CLEAR_FOR_SCOPE",
            hit_subject: "OWNER_CASE_IN_REVIEW",
            ghost: "OWNER_NOT_SCREENED",
        }
        assert "designated" not in data and "RESTRICTED" not in out.text
        assert (
            http.post(f"{BASE}/ownership/rollup", json={"owners": []}, headers=owner).json()[
                "status"
            ]
            == "INCOMPLETE"
        )  # no owner is never clear
        assert http.post(f"{BASE}/ownership/rollup", json=body).status_code in (401, 403)


def test_reviewers_ask_for_information_and_someone_else_reviews_the_answer(
    rt_url: str, owner_url: str
) -> None:
    o, who = _people(rt_url)
    tenant = str(uuid.uuid4())
    ids = _seed(rt_url, tenant)
    with TestClient(_app(rt_url, o), base_url=ISS) as api:

        def call(method: str, path: str, person: str, **kw: Any) -> Any:
            return api.request(method, path, headers=o.bearer(who[person]), **kw)

        for person, capability in (
            ("analyst", "screening.case.analyst"),
            ("senior_a", "screening.case.senior"),
            ("senior_b", "screening.case.senior"),
        ):
            _grant(api, o, who, person, capability)

        url = f"{Q}/cases/{ids['hit']}/information-requests"
        assert call("GET", url, "stranger").status_code == 403
        assert call("POST", url, "stranger", json=ITEMS).status_code == 403
        assert (
            call(
                "POST", url, "analyst", json={"items": ["bank_password"], "due_in_days": 20}
            ).status_code
            == 422
        )
        assert (
            call(
                "POST", url, "analyst", json={"items": ["SOURCE_OF_FUNDS"], "due_in_days": 1}
            ).status_code
            == 422
        )

        made = call("POST", url, "analyst", json=ITEMS)
        assert made.status_code == 201 and made.json()["state"] == "REQUESTED"
        again = call("POST", url, "senior_a", json=ITEMS)
        assert again.status_code == 409 and again.json()["error"]["code"] == "REQUEST_ALREADY_OPEN"
        edd_id = made.json()["edd_id"]

        listed = call("GET", url, "senior_a").json()["items"]
        assert [i["edd_id"] for i in listed] == [edd_id] and listed[0]["requested_by"] == who[
            "analyst"
        ]

        review = f"{Q}/information-requests/{edd_id}:review"
        assert (
            call("POST", review, "analyst", json={"accepted": True}).status_code == 403
        )  # an analyst does not review
        early = call("POST", review, "senior_a", json={"accepted": True})
        assert early.status_code == 409 and early.json()["error"]["code"] == "NOTHING_TO_REVIEW"

        _reply(rt_url, tenant, ids["hit"], ITEMS["items"])
        need_code = call("POST", review, "senior_a", json={"accepted": False})
        assert (
            need_code.status_code == 422
            and need_code.json()["error"]["code"] == "REVIEW_CODE_REQUIRED"
        )
        done = call("POST", review, "senior_a", json={"accepted": True})
        assert done.status_code == 200 and done.json()["state"] == "ACCEPTED"
        assert call("POST", review, "senior_a", json={"accepted": True}).json()["error"][
            "code"
        ] in ("NOTHING_TO_REVIEW", "REQUEST_CLOSED")

        # the person who asked is never the one who reviews
        other_url = f"{Q}/cases/{ids['other']}/information-requests"
        asked = call("POST", other_url, "senior_b", json=ITEMS).json()
        _reply(rt_url, tenant, ids["other"], ITEMS["items"])
        own = call(
            "POST",
            f"{Q}/information-requests/{asked['edd_id']}:review",
            "senior_b",
            json={"accepted": True},
        )
        assert own.status_code == 403 and own.json()["error"]["code"] == "REVIEWER_ASKED_FOR_IT"
        assert (
            call(
                "POST",
                f"{Q}/information-requests/{asked['edd_id']}:review",
                "senior_a",
                json={"accepted": False, "code": "UNREADABLE"},
            ).json()["state"]
            == "INSUFFICIENT"
        )
        assert (
            call("GET", f"{Q}/cases/{uuid.uuid4()}/information-requests", "senior_a").status_code
            == 404
        )

    kinds = {(e["event_type"], e["outcome"]) for e in _audit(owner_url)}
    for expected in (
        ("screening.case.information_requested", "SUCCESS"),
        ("screening.case.information_reviewed", "SUCCESS"),
        ("screening.case.refused", "DENIED"),
    ):
        assert expected in kinds, expected
