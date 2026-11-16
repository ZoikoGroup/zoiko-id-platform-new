"""Ownership graphs over HTTP against PostgreSQL (DTV-24, CR-102): analyze without storing, record immutable versions, refuse a loop, never name an owner."""

from __future__ import annotations

import asyncio

from conftest import engine
from sqlalchemy import text
from test_v2_facade_pg import world

URL = "/v2/ownership-graphs"
ENTITIES = [
    {"id": "target", "kind": "COMPANY"},
    {"id": "holdco", "kind": "COMPANY"},
    {"id": "p1", "kind": "PERSON"},
    {"id": "p2", "kind": "PERSON"},
]
LINKS = [
    {"owner": "holdco", "owned": "target", "percent": "50"},
    {"owner": "p1", "owned": "holdco", "percent": "60"},
    {"owner": "p2", "owned": "target", "percent": "30"},
]
BODY = {"target": "target", "entities": ENTITIES, "links": LINKS, "threshold": "25"}


def test_analyze_stores_nothing_and_says_candidates_never_owners(rt_url: str) -> None:
    with world(rt_url) as (http, mine, _t, _a):
        assert http.post(f"{URL}:analyze", json=BODY).status_code == 401
        out = http.post(f"{URL}:analyze", headers=mine, json=BODY)
        assert out.status_code == 200, out.text
        data = out.json()
        by = {c["entity_id"]: c for c in data["candidates"]}
        assert (
            by["p1"]["effective_percent"] == "30" and by["p2"]["effective_percent"] == "30"
        )  # 60% of 50%, and 30% direct
        assert data["recommendation"] == "REVIEW" and any(
            "IS a beneficial owner" in n for n in data["notes"]
        )
        assert http.get(URL, headers=mine).json()["items"] == []  # nothing was kept


def test_a_graph_is_recorded_as_immutable_versions_and_read_back_with_its_findings(
    rt_url: str,
) -> None:
    with world(rt_url) as (http, mine, _t, _a):
        first = http.post(URL, headers=mine, json=BODY)
        assert first.status_code == 201, first.text
        assert first.json()["version"] == 1 and len(first.json()["content_hash"]) == 64
        edited = {
            **BODY,
            "links": [*LINKS[:2], {"owner": "p2", "owned": "target", "percent": "10"}],
        }
        second = http.post(URL, headers=mine, json=edited).json()
        assert second["version"] == 2 and second["content_hash"] != first.json()["content_hash"]
        listed = http.get(URL, headers=mine).json()["items"]
        assert [i["version"] for i in listed] == [2, 1]
        one = http.get(f"{URL}/{first.json()['graph_id']}", headers=mine).json()
        assert {c["entity_id"] for c in one["findings"]["candidates"]} == {
            "p1",
            "p2",
        }  # version 1 still reads as it was
        assert http.get(f"{URL}/not-a-uuid", headers=mine).status_code == 404
        assert (
            http.get(f"{URL}/00000000-0000-4000-8000-000000000000", headers=mine).status_code == 404
        )


def test_a_loop_or_an_over_owned_company_is_refused_and_never_stored(rt_url: str) -> None:
    with world(rt_url) as (http, mine, _t, _a):
        loop = {
            **BODY,
            "links": [
                {"owner": "holdco", "owned": "target", "percent": "50"},
                {"owner": "target", "owned": "holdco", "percent": "50"},
            ],
        }
        out = http.post(URL, headers=mine, json=loop)
        assert out.status_code == 422 and out.json()["code"] == "ownership_cycle", out.text
        over = {
            **BODY,
            "links": [
                {"owner": "p1", "owned": "target", "percent": "70"},
                {"owner": "p2", "owned": "target", "percent": "40"},
            ],
        }
        assert http.post(URL, headers=mine, json=over).json()["code"] == "owned_over_100_percent"
        assert (
            http.post(URL, headers=mine, json={**BODY, "threshold": "abc"}).json()["code"]
            == "threshold_invalid"
        )
        assert http.get(URL, headers=mine).json()["items"] == []


def test_a_recorded_graph_never_changes(rt_url: str) -> None:
    from conftest import ctx

    with world(rt_url) as (http, mine, tenant, _a):
        http.post(URL, headers=mine, json=BODY)

        async def tamper() -> str:
            async with engine(rt_url) as eng:
                try:
                    async with eng.begin() as c:
                        await c.execute(
                            text("SELECT set_config('app.tenant_id', :t, true)"), {"t": tenant}
                        )
                        await c.execute(text("UPDATE org.ownership_graph SET threshold = 1"))
                except Exception as e:  # noqa: BLE001
                    return str(e)
            return "no error"

        msg = asyncio.run(tamper())
        assert "never changes" in msg or "permission denied" in msg, msg
        _ = ctx


# ------------------------------------------------------------------------------------------- a person decides on a candidate (CR-103)
REASON = "Passport and share register seen; holds 30 percent through the holding company."


def test_the_person_who_recorded_a_graph_cannot_decide_its_candidates_and_only_candidates_can_be_decided(
    rt_url: str,
) -> None:
    with world(rt_url) as (http, mine, _t, _a):
        graph = http.post(URL, headers=mine, json=BODY).json()
        path = f"{URL}/{graph['graph_id']}/confirmations"
        same = http.post(
            path,
            headers=mine,
            json={
                "entity_id": "p1",
                "decision": "CONFIRMED",
                "reason": REASON,
                "evidence_ref": "doc:register-1",
            },
        )
        assert same.status_code == 403 and same.json()["code"] == "reviewer_is_author", same.text
        assert (
            http.post(
                f"{URL}/not-a-uuid/confirmations",
                headers=mine,
                json={
                    "entity_id": "p1",
                    "decision": "CONFIRMED",
                    "reason": REASON,
                    "evidence_ref": "x",
                },
            ).status_code
            == 404
        )
        assert (
            http.post(
                path,
                json={
                    "entity_id": "p1",
                    "decision": "CONFIRMED",
                    "reason": REASON,
                    "evidence_ref": "x",
                },
            ).status_code
            == 401
        )
        assert (
            http.post(
                path,
                headers=mine,
                json={
                    "entity_id": "p1",
                    "decision": "MAYBE",
                    "reason": REASON,
                    "evidence_ref": "x",
                },
            ).status_code
            == 422
        )


def test_a_different_person_decides_once_with_a_reason_and_evidence_and_it_never_changes(
    rt_url: str,
) -> None:
    from datetime import UTC, datetime

    from conftest import ctx
    from zid_db import tenant_transaction
    from zid_platform.modules.organization.application import ownership_service as svc
    from zid_platform.modules.organization.domain.ownership import OwnershipError

    with world(rt_url) as (http, mine, tenant, _author):
        graph = http.post(URL, headers=mine, json=BODY).json()
        reviewer = "00000000-0000-4000-8000-0000000000aa"

        async def run() -> list[str]:
            codes: list[str] = []
            async with engine(rt_url) as eng:

                async def go(**over):  # type: ignore[no-untyped-def]
                    args = {
                        "entity_id": "p1",
                        "decision": "CONFIRMED",
                        "reason": REASON,
                        "evidence_ref": "doc:register-1",
                        "reviewer": reviewer,
                        **over,
                    }
                    async with tenant_transaction(eng, ctx(tenant)) as c:
                        return await svc.confirm(
                            c, tenant, graph["graph_id"], now=datetime.now(UTC), **args
                        )

                done = await go()
                assert done["decision"] == "CONFIRMED" and done["reviewer"] == reviewer
                for over in (
                    {},
                    {"entity_id": "holdco"},
                    {"entity_id": "p2", "reason": "short"},
                    {"entity_id": "p2", "evidence_ref": " "},
                    {"entity_id": "nobody"},
                ):
                    try:
                        await go(**over)
                    except OwnershipError as e:
                        codes.append(e.code)
            return codes

        codes = asyncio.run(run())
        assert codes == [
            "ALREADY_DECIDED",
            "NOT_A_CANDIDATE",
            "REASON_TOO_SHORT",
            "EVIDENCE_REF_REQUIRED",
            "NOT_A_CANDIDATE",
        ]
        read = http.get(f"{URL}/{graph['graph_id']}", headers=mine).json()
        assert [c["entity_id"] for c in read["confirmations"]] == ["p1"] and read["confirmations"][
            0
        ]["evidence_ref"] == "doc:register-1"

        async def tamper() -> str:
            async with engine(rt_url) as eng:
                try:
                    async with eng.begin() as c:
                        await c.execute(
                            text("SELECT set_config('app.tenant_id', :t, true)"), {"t": tenant}
                        )
                        await c.execute(
                            text("UPDATE org.ownership_confirmation SET decision = 'NOT_CONFIRMED'")
                        )
                except Exception as e:  # noqa: BLE001
                    return str(e)
            return "no error"

        msg = asyncio.run(tamper())
        assert "never changes" in msg or "permission denied" in msg, msg
