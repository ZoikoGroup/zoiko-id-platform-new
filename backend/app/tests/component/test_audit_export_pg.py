"""The SIEM audit feed on a real database: oldest first, a cursor that never skips or repeats a record, JSON and NDJSON, and who may read it."""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient
from onboarding_fixture import AUD, ISS, onboard
from zid_platform.main import create_app
from zid_platform.modules.evidence.api import evidence as evidence_api
from zid_platform.settings import Settings


def app_for(rt_url: str, o: Any) -> Any:
    return create_app(
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


@pytest.fixture(autouse=True)
def no_settle_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    from datetime import timedelta

    monkeypatch.setattr(
        evidence_api, "SETTLE", timedelta(0)
    )  # the records of this test are only moments old


def follow(api: TestClient, headers: dict[str, str], page: int) -> list[dict[str, Any]]:
    seen: list[dict[str, Any]] = []
    cursor = ""
    for _ in range(200):
        r = api.get(
            "/v1/evidence/audit-events/export",
            params={"after": cursor, "limit": page},
            headers=headers,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        seen += body["items"]
        cursor = body["next_cursor"]
        if not body["has_more"]:
            return seen
    raise AssertionError("the feed never ended")


def test_the_feed_is_complete_ordered_and_resumable(rt_url: str) -> None:
    o = onboard(rt_url, extra_actions=["evidence.read"], extra_resources=["audit_record:*"])
    h = o.bearer(o.owner)
    with TestClient(app_for(rt_url, o), base_url=ISS) as api:
        everything = follow(api, h, 1000)
        assert len(everything) >= 3 and {i["schema"] for i in everything} == {"zid.audit.v1"}
        stamps = [(i["created_at"], i["audit_id"]) for i in everything]
        assert stamps == sorted(stamps)  # oldest first, with the id breaking ties
        assert len({i["audit_id"] for i in everything}) == len(everything)

        # following it a page at a time gives exactly the same records: nothing skipped, nothing repeated
        assert [i["audit_id"] for i in follow(api, h, 2)] == [i["audit_id"] for i in everything]

        # with nothing new the cursor stays put, so polling again is harmless
        last = api.get("/v1/evidence/audit-events/export", params={"limit": 1000}, headers=h).json()
        again = api.get(
            "/v1/evidence/audit-events/export", params={"after": last["next_cursor"]}, headers=h
        ).json()
        assert (
            again["items"] == []
            and again["next_cursor"] == last["next_cursor"]
            and again["has_more"] is False
        )

        # NDJSON: one record per line, the cursor in a header
        nd = api.get(
            "/v1/evidence/audit-events/export", params={"format": "ndjson", "limit": 2}, headers=h
        )
        lines = [json.loads(line) for line in nd.text.splitlines()]
        assert nd.headers["content-type"].startswith("application/x-ndjson") and len(lines) == 2
        assert [x["audit_id"] for x in lines] == [i["audit_id"] for i in everything[:2]]
        assert nd.headers["x-next-cursor"] and nd.headers["x-has-more"] == "true"

        # a filter, and a cursor that is not one of ours
        only = api.get(
            "/v1/evidence/audit-events/export",
            params={"event_type": everything[0]["event_type"], "limit": 1000},
            headers=h,
        ).json()["items"]
        assert only and {i["event_type"] for i in only} == {everything[0]["event_type"]}
        assert (
            api.get(
                "/v1/evidence/audit-events/export", params={"after": "not-a-cursor"}, headers=h
            ).status_code
            == 422
        )


def test_only_someone_who_may_read_evidence_can_follow_the_feed(rt_url: str) -> None:
    o = onboard(rt_url)  # no evidence.read authority
    with TestClient(app_for(rt_url, o), base_url=ISS) as api:
        assert (
            api.get("/v1/evidence/audit-events/export", headers=o.bearer(o.owner)).status_code
            == 403
        )
