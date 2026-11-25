"""Verify API results and rechecks (CR-155), against the real database and the real HTTP endpoints.

A recheck is a NEW session that follows a finished one, with its own link and decision; the original and its decisions are never changed. The company's console searches its sessions across applications,
opens one to see its decision history, and may start a recheck. Everything is the minimized view: no photograph, document, score or reviewer note.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from test_verify_api_go_live_pg import Company, uid
from zid_platform.modules.verify_api.application import service


def server(
    co: Company, name: str, scopes: list[str] | None = None, origins: list[str] | None = None
) -> dict[str, str]:
    """A sandbox application of this company and the bearer headers its server would use."""

    async def seed() -> dict[str, Any]:
        async with co.tx() as c:
            a = await service.create_application(
                c,
                co.o.tenant,
                uid(),
                name=name,
                environment="SANDBOX",
                scopes=scopes,
                origins=origins,
            )
            return {
                "c": await service.issue_credential(c, co.o.tenant, uid(), a["application_id"]),
                "id": a["application_id"],
            }

    made = asyncio.run(seed())
    r = co.http.post(
        "/v1/verify-api/oauth2/token",
        json={"client_id": made["c"]["client_id"], "client_secret": made["c"]["client_secret"]},
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}", "X-App": made["id"]}


def hdr(h: dict[str, str]) -> dict[str, str]:
    return {"Authorization": h["Authorization"]}


def fixture(co: Company, h: dict[str, str], name: str, ref: str) -> str:
    r = co.http.post(
        f"/v1/verify-api/sandbox/fixtures/{name}/sessions",
        headers={**hdr(h), "Idempotency-Key": uid()},
        json={"customer_reference": ref},
    )
    assert r.status_code == 201, r.text
    return str(r.json()["session"]["session_id"])


def code(r: Any) -> str:
    return str(r.json()["error"]["code"])


def recheck(co: Company, h: dict[str, str], sid: str, key: str | None = None) -> Any:
    return co.http.post(
        f"/v1/verify-api/verification-sessions/{sid}:recheck",
        headers={**hdr(h), **({"Idempotency-Key": key} if key else {})},
    )


def test_a_recheck_is_a_new_session_that_follows_a_finished_one_and_leaves_the_original_untouched(
    rt_url: str, owner_url: str
) -> None:
    co = Company(rt_url)
    try:
        h = server(co, "Checkout")
        done = fixture(co, h, "approved", "person-1")
        before = co.http.get(
            f"/v1/verify-api/verification-sessions/{done}/decision", headers=hdr(h)
        ).json()

        key = uid()
        r = recheck(co, h, done, key)
        assert r.status_code == 201 and r.headers["cache-control"] == "no-store", r.text
        view = r.json()
        assert (
            view["recheck_of"] == done and view["session_id"] != done and view["status"] == "ready"
        )
        assert view["customer_reference"] == "person-1" and view["launch_url"].startswith(
            "https://console.example.com/verify/session/zvl_"
        )
        # safe to repeat: the same key is the same session; a different original under that key is refused
        again = recheck(co, h, done, key)
        assert again.status_code == 200 and again.json()["session_id"] == view["session_id"]
        other = fixture(co, h, "declined", "person-2")
        assert code(recheck(co, h, other, key)) == "INVALID_IDEMPOTENCY_REUSE"
        assert recheck(co, h, done, None).status_code == 422  # a key is required

        # the person finishes the recheck; it has its own decision and the original is exactly as it was
        token = view["launch_url"].rsplit("/", 1)[-1]
        # (the second link from the repeat replaced the first, so use the one that is live now)
        live = again.json()["launch_url"].rsplit("/", 1)[-1]
        assert token != live
        assert (
            co.http.post(
                f"/v1/verify-api/hosted/{live}:simulate", json={"outcome": "declined"}
            ).status_code
            == 200
        )
        new_decision = co.http.get(
            f"/v1/verify-api/verification-sessions/{view['session_id']}/decision", headers=hdr(h)
        ).json()
        assert new_decision["outcome"] == "not_verified" and new_decision["decision_version"] == 1
        after = co.http.get(
            f"/v1/verify-api/verification-sessions/{done}/decision", headers=hdr(h)
        ).json()
        assert after == before and after["outcome"] == "verified"
    finally:
        co.close()


def test_only_a_session_that_has_ended_can_be_rechecked_and_only_by_the_application_that_made_it(
    rt_url: str, owner_url: str
) -> None:
    co = Company(rt_url)
    try:
        h = server(co, "Checkout")
        for name in ("approved", "declined", "inconclusive", "expired"):
            sid = fixture(co, h, name, f"p-{name}")
            r = recheck(co, h, sid, uid())
            # a finished, no-answer or ran-out-of-time session can be rechecked; a declined one is a final result and can too
            assert r.status_code == 201, (name, r.text)
        for name in ("review",):
            sid = fixture(co, h, name, "p-review")
            assert code(recheck(co, h, sid, uid())) == "SESSION_STATE_INVALID", (
                name
            )  # with a reviewer: wait for the decision
        live = co.http.post(
            "/v1/verify-api/verification-sessions",
            headers={**hdr(h), "Idempotency-Key": uid()},
            json={
                "customer_reference": "p-live",
                "country": "US",
                "purpose_code": "customer_onboarding",
            },
        ).json()["session_id"]
        assert code(recheck(co, h, live, uid())) == "SESSION_STATE_INVALID"  # still in progress
        co.http.post(f"/v1/verify-api/verification-sessions/{live}:cancel", headers=hdr(h))
        assert (
            code(recheck(co, h, live, uid())) == "SESSION_STATE_INVALID"
        )  # canceled sessions are not rechecked

        # another application of the same company cannot recheck it
        mine = fixture(co, h, "approved", "p-mine")
        h2 = server(co, "Other app")
        assert code(recheck(co, h2, mine, uid())) == "NOT_FOUND"
        # a token without the recheck scope is refused
        h3 = server(co, "Read only", scopes=["verifications:create", "verifications:read"])
        sid3 = fixture(co, h3, "approved", "p-3")
        r3 = recheck(co, h3, sid3, uid())
        assert r3.status_code == 403 and code(r3) == "TENANT_SCOPE_DENIED"
        assert recheck(co, {"Authorization": "Bearer zpt_nope"}, mine, uid()).status_code == 401
    finally:
        co.close()


def test_the_company_searches_its_sessions_across_applications_and_reads_one_with_its_history(
    rt_url: str, owner_url: str
) -> None:
    co = Company(rt_url)
    try:
        a = server(co, "Web checkout")
        b = server(co, "Mobile app")
        approved = fixture(co, a, "approved", "cust-A")
        review = fixture(co, a, "review", "cust-B")
        fixture(co, b, "declined", "cust-A")  # the same reference in another application
        co.http.post(
            f"/v1/verify-api/sandbox/sessions/{review}:simulate",
            headers=hdr(a),
            json={"outcome": "approved"},
        )  # review resolved: version 2

        def search(**params: Any) -> Any:
            return co.http.get("/v1/verify-api/sessions", headers=co.h, params=params)

        everything = search().json()
        assert len(everything["items"]) == 3 and {
            i["application_name"] for i in everything["items"]
        } == {"Web checkout", "Mobile app"}
        assert all("launch_url" not in i for i in everything["items"])
        assert [
            i["customer_reference"] for i in search(customer_reference="cust-A").json()["items"]
        ].count("cust-A") == 2  # exact match, both applications
        assert len(search(customer_reference="cust").json()["items"]) == 0  # never a text search
        assert {
            i["application_name"] for i in search(application_id=a["X-App"]).json()["items"]
        } == {"Web checkout"}
        assert (
            len(search(status="completed").json()["items"]) == 3
            and search(status="expired").json()["items"] == []
        )
        assert (
            search(environment="production").json()["items"] == []
            and len(search(environment="sandbox").json()["items"]) == 3
        )
        for bad in (
            {"status": "nope"},
            {"environment": "moon"},
            {"limit": 0},
            {"limit": 101},
            {"cursor": "!!"},
            {"application_id": "not-a-uuid"},
        ):
            assert search(**bad).status_code == 422, bad
        first = search(limit=2).json()
        rest = search(limit=2, cursor=first["next_cursor"]).json()
        assert len(first["items"]) == 2 and len(rest["items"]) == 1 and rest["next_cursor"] is None
        assert co.http.get("/v1/verify-api/sessions").status_code == 401

        # one session with its whole history: both decisions, the first kept, and no rechecks yet
        d = co.http.get(f"/v1/verify-api/sessions/{review}", headers=co.h).json()
        assert (
            d["application_name"] == "Web checkout"
            and d["can_recheck"] is True
            and d["rechecks"] == []
        )
        assert [x["decision_version"] for x in d["decisions"]] == [1, 2]
        assert [x["outcome"] for x in d["decisions"]] == ["review_required", "verified"] and d[
            "decisions"
        ][1]["supersedes_decision_id"] == d["decisions"][0]["decision_id"]
        for x in d["decisions"]:
            assert x["evidence_receipt_id"] is None and set(x["checks"][0]) == {"method", "result"}
        assert (
            co.http.get(f"/v1/verify-api/sessions/vfs_{uuid.uuid4().hex}", headers=co.h).status_code
            == 404
        )

        # the administrator starts a recheck: a new session that follows this one, link shown once
        made = co.http.post(f"/v1/verify-api/sessions/{approved}:recheck", headers=co.h)
        assert made.status_code == 201 and made.headers["cache-control"] == "no-store", made.text
        mv = made.json()
        assert mv["recheck_of"] == approved and mv["launch_url"].startswith(
            "https://console.example.com/verify/session/"
        )
        det = co.http.get(f"/v1/verify-api/sessions/{approved}", headers=co.h).json()
        assert [r["session_id"] for r in det["rechecks"]] == [
            mv["session_id"]
        ] and "launch_url" not in det
        child = co.http.get(f"/v1/verify-api/sessions/{mv['session_id']}", headers=co.h).json()
        assert (
            child["recheck_of"] == approved
            and child["can_recheck"] is False
            and child["decisions"] == []
        )  # still in progress
        assert (
            co.http.post(
                f"/v1/verify-api/sessions/{mv['session_id']}:recheck", headers=co.h
            ).status_code
            == 409
        )
        assert (
            co.http.post(
                f"/v1/verify-api/sessions/vfs_{uuid.uuid4().hex}:recheck", headers=co.h
            ).status_code
            == 404
        )
        assert co.http.post(f"/v1/verify-api/sessions/{approved}:recheck").status_code == 401
    finally:
        co.close()


def test_another_company_sees_none_of_it(rt_url: str, owner_url: str) -> None:
    co, other = Company(rt_url), Company(rt_url)
    try:
        h = server(co, "Checkout")
        sid = fixture(co, h, "approved", "cust-1")
        assert other.http.get("/v1/verify-api/sessions", headers=other.h).json()["items"] == []
        assert other.http.get(f"/v1/verify-api/sessions/{sid}", headers=other.h).status_code == 404
        assert (
            other.http.post(f"/v1/verify-api/sessions/{sid}:recheck", headers=other.h).status_code
            == 404
        )
    finally:
        co.close()
        other.close()
