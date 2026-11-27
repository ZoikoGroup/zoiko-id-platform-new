"""The Verify API's sessions, decisions and sandbox (CR-150), against the real database and the real HTTP endpoints.

A partner's server creates a session with an Idempotency-Key, gets a launch link, the person opens it and (in the sandbox) picks a simulated outcome, and the server reads a minimized decision. A session
belongs to the application that created it; the link is shown once and ends with the session; nothing real is ever checked in the sandbox.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from conftest import ctx, engine
from fastapi.testclient import TestClient
from onboarding_fixture import ISS, onboard
from sqlalchemy import text
from test_commercial_pg import settings
from zid_db import tenant_transaction
from zid_platform.main import create_app
from zid_platform.modules.verify_api.api import sessions as sessions_api
from zid_platform.modules.verify_api.application import service
from zid_platform.modules.verify_api.application.throttle import FailureLimiter
from zid_platform.modules.verify_api.domain import model as m
from zid_platform.modules.verify_api.domain import sessions as s

ORIGIN = "https://console.example.com"
GOOD = {
    "customer_reference": "customer-48201",
    "country": "US",
    "purpose_code": "customer_onboarding",
}


def uid() -> str:
    return str(uuid.uuid4())


class World:
    """One company with applications whose servers hold tokens, and an HTTP client for the whole app."""

    def __init__(self, rt_url: str) -> None:
        sessions_api._hosted_limiter = (
            FailureLimiter()
        )  # failed links in other tests must not count here
        self.rt_url = rt_url
        self.tenant = uid()
        self.app = create_app(
            settings(rt_url, verify_hosted_origin=ORIGIN), keyring=onboard(rt_url).ring
        )
        self.http = TestClient(self.app, base_url=ISS)
        self.http.__enter__()

    def close(self) -> None:
        self.http.__exit__(None, None, None)

    def server(
        self,
        name: str = "Checkout",
        scopes: list[str] | None = None,
        origins: list[str] | None = None,
        tenant: str | None = None,
    ) -> dict[str, str]:
        """An application with a credential, exchanged over HTTP for a bearer token: the headers its server would send."""
        tenant = tenant or self.tenant

        async def seed() -> dict[str, Any]:
            async with engine(self.rt_url) as eng:
                async with tenant_transaction(eng, ctx(tenant)) as t:
                    made = await service.create_application(
                        t,
                        tenant,
                        uid(),
                        name=name,
                        environment="SANDBOX",
                        scopes=scopes,
                        origins=origins,
                    )
                async with tenant_transaction(eng, ctx(tenant)) as t:
                    return {
                        "app": made,
                        "cred": await service.issue_credential(
                            t, tenant, uid(), made["application_id"]
                        ),
                    }

        made = asyncio.run(seed())
        r = self.http.post(
            "/v1/verify-api/oauth2/token",
            json={
                "client_id": made["cred"]["client_id"],
                "client_secret": made["cred"]["client_secret"],
            },
        )
        assert r.status_code == 200, r.text
        return {"Authorization": f"Bearer {r.json()['access_token']}"}

    def create(self, h: dict[str, str], key: str | None = None, **over: Any) -> Any:
        return self.http.post(
            "/v1/verify-api/verification-sessions",
            headers={**h, "Idempotency-Key": key or uid()},
            json={**GOOD, **over},
        )

    @staticmethod
    def link_token(view: dict[str, Any]) -> str:
        return view["launch_url"].rsplit("/", 1)[-1]


def code(r: Any) -> str:
    return str(r.json()["error"]["code"])


def test_a_session_is_created_with_a_launch_link_shown_once_and_the_person_finishes_it_on_the_test_page(
    rt_url: str, owner_url: str
) -> None:
    w = World(rt_url)
    try:
        h = w.server(origins=["https://app.example.com"])
        r = w.create(h, return_origin="https://app.example.com", metadata={"case": "signup"})
        assert r.status_code == 201 and r.headers["cache-control"] == "no-store", r.text
        view = r.json()
        sid = view["session_id"]
        assert (
            sid.startswith("vfs_")
            and view["status"] == "ready"
            and view["mode"] == "sandbox"
            and view["metadata"] == {"case": "signup"}
        )
        assert (
            view["launch_url"].startswith(f"{ORIGIN}/verify/session/zvl_")
            and view["launch_expires_at"]
            and view["workflow_version"] == s.CURRENT_VERSION
        )
        token = w.link_token(view)

        # the link is kept only as a hash, and is never read back
        async def stored() -> Any:
            async with engine(owner_url) as eng, eng.begin() as c:
                return (
                    await c.execute(
                        text(
                            "SELECT launch_token_hash FROM verify.api_session WHERE session_id = :s"
                        ),
                        {"s": sid},
                    )
                ).scalar_one()

        assert bytes(asyncio.run(stored())) == m.hash_secret(token)
        again = w.http.get(f"/v1/verify-api/verification-sessions/{sid}", headers=h).json()
        assert "launch_url" not in again and token not in str(again) and again["status"] == "ready"
        assert (
            w.http.get(f"/v1/verify-api/verification-sessions/{sid}/decision", headers=h).json()[
                "error"
            ]["code"]
            == "DECISION_NOT_AVAILABLE"
        )

        # the person opens the link: told who is asking, why, that this is a test; not the partner's reference, not the session id
        page = w.http.get(f"/v1/verify-api/hosted/{token}")
        assert page.status_code == 200 and page.headers["cache-control"] == "no-store"
        ctx_ = page.json()
        assert (
            ctx_["requested_by"] == "Checkout"
            and ctx_["sandbox"] is True
            and ctx_["purpose_code"] == "customer_onboarding"
            and "approved" in ctx_["outcomes"]
        )
        assert (
            "customer-48201" not in page.text and sid not in page.text and ctx_["status"] == "ready"
        )
        assert w.http.post(f"/v1/verify-api/hosted/{token}:open").json()["status"] == "opened"
        assert (
            w.http.get(f"/v1/verify-api/verification-sessions/{sid}", headers=h).json()["status"]
            == "opened"
        )

        done = w.http.post(f"/v1/verify-api/hosted/{token}:simulate", json={"outcome": "approved"})
        assert done.status_code == 200 and done.json()["sandbox"] is True
        # the link ends with the session
        assert code(w.http.get(f"/v1/verify-api/hosted/{token}")) == "LAUNCH_INVALID"
        assert (
            code(
                w.http.post(f"/v1/verify-api/hosted/{token}:simulate", json={"outcome": "declined"})
            )
            == "LAUNCH_INVALID"
        )

        # the partner's server reads the minimized decision: an outcome, check results and codes, nothing that identifies or measures the person
        d = w.http.get(f"/v1/verify-api/verification-sessions/{sid}/decision", headers=h).json()
        assert (
            d["outcome"] == "verified"
            and d["status"] == "completed"
            and d["sandbox"] is True
            and d["decision_version"] == 1
            and d["supersedes_decision_id"] is None
        )
        assert d["checks"] == [
            {"method": "face_match", "result": "pass"}
        ]  # the one check the product has, in the sandbox as in production
        assert (
            d["evidence_receipt_id"] is None
            and d["assurance"]["level"] == "SANDBOX_SIMULATED"
            and d["policy_version"] == "pol_sandbox_simulated"
        )
        assert set(d) == {
            "session_id", "decision_id", "status", "outcome", "assurance", "checks", "reason_codes", "evidence_receipt_id", "policy_version", "region", "completed_at", "decision_version", "supersedes_decision_id", "sandbox",
        }  # fmt: skip
        assert w.http.get(f"/v1/verify-api/verification-sessions/{sid}", headers=h).json()[
            "completed_at"
        ]
    finally:
        w.close()


def test_creating_a_session_is_safe_to_repeat_and_a_changed_request_under_the_same_key_is_refused(
    rt_url: str, owner_url: str
) -> None:
    w = World(rt_url)
    try:
        h = w.server()
        first = w.create(h, key="order-2026-11-24-0001")
        assert first.status_code == 201
        a = first.json()
        # the same key and request: the same session, not a second one, and a fresh link because the first was shown once
        second = w.create(h, key="order-2026-11-24-0001")
        assert (
            second.status_code == 200
            and second.json()["session_id"] == a["session_id"]
            and second.json()["launch_url"] != a["launch_url"]
        )
        assert (
            code(w.http.get(f"/v1/verify-api/hosted/{w.link_token(a)}")) == "LAUNCH_INVALID"
        )  # the first link stopped
        assert w.http.get(f"/v1/verify-api/hosted/{w.link_token(second.json())}").status_code == 200
        # same key, different request
        assert (
            code(w.create(h, key="order-2026-11-24-0001", customer_reference="someone-else"))
            == "INVALID_IDEMPOTENCY_REUSE"
        )
        # a different key is a different operation
        assert w.create(h, key="order-2026-11-24-0002").json()["session_id"] != a["session_id"]
        listed = w.http.get("/v1/verify-api/verification-sessions", headers=h).json()["items"]
        assert len(listed) == 2  # one per operation, never one per attempt
        # a missing or malformed key is refused before anything is created
        for bad in (None, "short", "has space in it"):
            r = w.http.post(
                "/v1/verify-api/verification-sessions",
                headers={**h, **({"Idempotency-Key": bad} if bad else {})},
                json=GOOD,
            )
            assert r.status_code == 422 and code(r) == "VALIDATION_FAILED", bad
        assert (
            len(w.http.get("/v1/verify-api/verification-sessions", headers=h).json()["items"]) == 2
        )

        # a key older than its window stands for a new request
        async def age() -> None:
            async with engine(owner_url) as eng, eng.begin() as c:
                await c.execute(
                    text(
                        "UPDATE verify.api_idempotency SET created_at = now() - interval '25 hours' WHERE idem_key = 'order-2026-11-24-0001'"
                    )
                )

        asyncio.run(age())
        fresh = w.create(h, key="order-2026-11-24-0001", customer_reference="someone-else")
        assert fresh.status_code == 201 and fresh.json()["session_id"] != a["session_id"]
    finally:
        w.close()


def test_a_bad_request_is_refused_before_anything_is_created(rt_url: str, owner_url: str) -> None:
    w = World(rt_url)
    try:
        h = w.server(origins=["https://app.example.com"])
        cases = [
            ({"country": "usa"}, 422, "VALIDATION_FAILED"),
            ({"purpose_code": "marketing"}, 422, "VALIDATION_FAILED"),
            ({"customer_reference": ""}, 422, "VALIDATION_FAILED"),
            ({"workflow_id": "wf_nope"}, 422, "WORKFLOW_VERSION_UNAVAILABLE"),
            ({"workflow_version": "1999-01"}, 422, "WORKFLOW_VERSION_UNAVAILABLE"),
            (
                {"return_origin": "https://evil.example.net"},
                422,
                "RETURN_ORIGIN_NOT_REGISTERED",
            ),  # a redirect target that was never registered
            ({"locale": "english"}, 422, "VALIDATION_FAILED"),
        ]
        for over, status, c in cases:
            r = w.create(h, **over)
            assert (r.status_code, code(r)) == (status, c), (over, r.text)
        assert (
            w.http.post(
                "/v1/verify-api/verification-sessions",
                headers={**h, "Idempotency-Key": uid()},
                json={**GOOD, "bogus": 1},
            ).status_code
            == 422
        )
        assert w.http.get("/v1/verify-api/verification-sessions", headers=h).json()["items"] == []
        assert w.create(h, return_origin="https://app.example.com").status_code == 201
    finally:
        w.close()


def test_every_simulated_outcome_gives_its_own_session_state_and_a_review_resolved_later_supersedes_the_first_decision(
    rt_url: str, owner_url: str
) -> None:
    w = World(rt_url)
    try:
        h = w.server()

        def fixture(outcome: str, **kw: Any) -> Any:
            return w.http.post(
                f"/v1/verify-api/sandbox/fixtures/{outcome}/sessions",
                headers={**h, "Idempotency-Key": uid()},
                json=kw,
            )

        expected = {
            "approved": ("completed", "verified"),
            "declined": ("completed", "not_verified"),
            "inconclusive": ("inconclusive", "inconclusive"),
            "review": ("manual_review", "review_required"),
        }
        for name, (status, outcome) in expected.items():
            r = fixture(name)
            assert r.status_code == 201, r.text
            body = r.json()
            assert (
                body["session"]["status"] == status
                and body["decision"]["outcome"] == outcome
                and body["decision"]["sandbox"] is True
            )
            assert all(rc.startswith("SIMULATED_") for rc in body["decision"]["reason_codes"])
        gone = fixture("expired").json()
        assert gone["session"]["status"] == "expired" and gone["decision"] is None
        assert (
            code(
                w.http.get(
                    f"/v1/verify-api/verification-sessions/{gone['session']['session_id']}/decision",
                    headers=h,
                )
            )
            == "DECISION_NOT_AVAILABLE"
        )
        assert code(fixture("verified")) == "VALIDATION_FAILED"

        # a review resolves later: the new decision supersedes the first, and the first is kept
        rev = fixture("review", customer_reference="needs-review").json()["session"]["session_id"]
        first = w.http.get(f"/v1/verify-api/verification-sessions/{rev}/decision", headers=h).json()
        sim = w.http.post(
            f"/v1/verify-api/sandbox/sessions/{rev}:simulate",
            headers=h,
            json={"outcome": "approved"},
        )
        assert sim.status_code == 200 and sim.json()["session"]["status"] == "completed"
        second = w.http.get(
            f"/v1/verify-api/verification-sessions/{rev}/decision", headers=h
        ).json()
        assert (
            second["decision_version"],
            second["supersedes_decision_id"],
            second["outcome"],
        ) == (2, first["decision_id"], "verified")

        async def versions() -> list[tuple[int, str]]:
            async with engine(owner_url) as eng, eng.begin() as c:
                rows = (
                    await c.execute(
                        text(
                            "SELECT decision_version, outcome FROM verify.api_decision WHERE session_id = :s ORDER BY decision_version"
                        ),
                        {"s": rev},
                    )
                ).all()
            return [(int(a), str(b)) for a, b in rows]

        assert asyncio.run(versions()) == [
            (1, "review_required"),
            (2, "verified"),
        ]  # nothing was overwritten
        # a finished session takes no further outcome
        assert (
            code(
                w.http.post(
                    f"/v1/verify-api/sandbox/sessions/{rev}:simulate",
                    headers=h,
                    json={"outcome": "declined"},
                )
            )
            == "SESSION_STATE_INVALID"
        )
        # a fixture call is repeatable too: the same key returns the same session
        key = uid()
        a1 = w.http.post(
            "/v1/verify-api/sandbox/fixtures/approved/sessions",
            headers={**h, "Idempotency-Key": key},
            json={},
        )
        a2 = w.http.post(
            "/v1/verify-api/sandbox/fixtures/approved/sessions",
            headers={**h, "Idempotency-Key": key},
            json={},
        )
        assert (
            a1.status_code == 201
            and a2.status_code == 200
            and a1.json()["session"]["session_id"] == a2.json()["session"]["session_id"]
            and a2.json()["created"] is False
        )
    finally:
        w.close()


def test_an_application_sees_only_its_own_sessions_and_a_token_only_what_its_scopes_allow(
    rt_url: str, owner_url: str
) -> None:
    w = World(rt_url)
    try:
        one = w.server("App one")
        two = w.server("App two")  # same company, another application
        stranger = w.server("Other company app", tenant=uid())
        sid = w.create(one).json()["session_id"]
        for h, who in (
            (two, "another application of the same company"),
            (stranger, "another company"),
        ):
            for path in (
                f"/v1/verify-api/verification-sessions/{sid}",
                f"/v1/verify-api/verification-sessions/{sid}/decision",
            ):
                assert code(w.http.get(path, headers=h)) == "NOT_FOUND", who
            assert (
                code(w.http.post(f"/v1/verify-api/verification-sessions/{sid}:cancel", headers=h))
                == "NOT_FOUND"
            ), who
            assert (
                code(
                    w.http.post(
                        f"/v1/verify-api/verification-sessions/{sid}/resume-token", headers=h
                    )
                )
                == "NOT_FOUND"
            ), who
            assert (
                code(
                    w.http.post(
                        f"/v1/verify-api/sandbox/sessions/{sid}:simulate",
                        headers=h,
                        json={"outcome": "approved"},
                    )
                )
                == "NOT_FOUND"
            ), who
            assert (
                w.http.get("/v1/verify-api/verification-sessions", headers=h).json()["items"] == []
            )
        assert [
            x["session_id"]
            for x in w.http.get("/v1/verify-api/verification-sessions", headers=one).json()["items"]
        ] == [sid]

        # a token carries only the scopes asked for: read-only cannot create; create-only cannot read
        reader = w.server(
            "Reader app", scopes=["verifications:read", "decisions:read", "verifications:list"]
        )
        r = w.create(reader)
        assert r.status_code == 403 and code(r) == "TENANT_SCOPE_DENIED"
        writer = w.server("Writer app", scopes=["verifications:create"])
        made = w.create(writer)
        assert made.status_code == 201
        assert (
            code(
                w.http.get(
                    f"/v1/verify-api/verification-sessions/{made.json()['session_id']}",
                    headers=writer,
                )
            )
            == "TENANT_SCOPE_DENIED"
        )
        assert (
            code(w.http.get("/v1/verify-api/verification-sessions", headers=writer))
            == "TENANT_SCOPE_DENIED"
        )
        assert code(w.http.get("/v1/verify-api/workflows", headers=writer)) == "TENANT_SCOPE_DENIED"
        assert (
            w.http.get("/v1/verify-api/verification-sessions").status_code == 401
        )  # and no token at all
    finally:
        w.close()


def test_sessions_are_listed_newest_first_page_by_page_with_fixed_filters(
    rt_url: str, owner_url: str
) -> None:
    w = World(rt_url)
    try:
        h = w.server()
        ids = [w.create(h, customer_reference=f"c-{i}").json()["session_id"] for i in range(5)]
        w.http.post(
            "/v1/verify-api/sandbox/fixtures/declined/sessions",
            headers={**h, "Idempotency-Key": uid()},
            json={"customer_reference": "c-done"},
        )
        seen: list[str] = []
        cursor = None
        for _ in range(10):
            page = w.http.get(
                "/v1/verify-api/verification-sessions",
                headers=h,
                params={"limit": 2, **({"cursor": cursor} if cursor else {})},
            ).json()
            seen += [x["session_id"] for x in page["items"]]
            cursor = page["next_cursor"]
            if not cursor:
                break
        assert (
            len(seen) == 6 and len(set(seen)) == 6 and set(ids) < set(seen)
        )  # no repeats, no gaps
        assert seen[-1] == ids[0]  # oldest last
        ready = w.http.get(
            "/v1/verify-api/verification-sessions", headers=h, params={"status": "ready"}
        ).json()["items"]
        assert len(ready) == 5 and {x["status"] for x in ready} == {"ready"}
        one = w.http.get(
            "/v1/verify-api/verification-sessions", headers=h, params={"customer_reference": "c-3"}
        ).json()["items"]
        assert [x["customer_reference"] for x in one] == ["c-3"]
        for params in (
            {"status": "bogus"},
            {"limit": 0},
            {"limit": 101},
            {"cursor": "!!not-a-cursor!!"},
        ):
            assert (
                w.http.get(
                    "/v1/verify-api/verification-sessions", headers=h, params=params
                ).status_code
                == 422
            ), params
        future = (datetime.now(UTC) + timedelta(days=1)).isoformat()
        assert (
            w.http.get(
                "/v1/verify-api/verification-sessions", headers=h, params={"created_from": future}
            ).json()["items"]
            == []
        )
    finally:
        w.close()


def test_a_session_can_be_canceled_resumed_and_expires_on_its_own(
    rt_url: str, owner_url: str
) -> None:
    w = World(rt_url)
    try:
        h = w.server()
        v = w.create(h).json()
        sid, old = v["session_id"], w.link_token(v)
        # resume: a fresh link, the old one stops
        resumed = w.http.post(f"/v1/verify-api/verification-sessions/{sid}/resume-token", headers=h)
        assert resumed.status_code == 200 and resumed.headers["cache-control"] == "no-store"
        new = w.link_token(resumed.json())
        assert (
            new != old
            and code(w.http.get(f"/v1/verify-api/hosted/{old}")) == "LAUNCH_INVALID"
            and w.http.get(f"/v1/verify-api/hosted/{new}").status_code == 200
        )
        # cancel: the session ends, the link is dead, it cannot be canceled or resumed again
        c = w.http.post(f"/v1/verify-api/verification-sessions/{sid}:cancel", headers=h)
        assert c.status_code == 200 and c.json()["status"] == "canceled"
        assert code(w.http.get(f"/v1/verify-api/hosted/{new}")) == "LAUNCH_INVALID"
        assert (
            code(w.http.post(f"/v1/verify-api/verification-sessions/{sid}:cancel", headers=h))
            == "SESSION_STATE_INVALID"
        )
        assert (
            code(w.http.post(f"/v1/verify-api/verification-sessions/{sid}/resume-token", headers=h))
            == "SESSION_STATE_INVALID"
        )
        assert (
            code(
                w.http.post(
                    f"/v1/verify-api/sandbox/sessions/{sid}:simulate",
                    headers=h,
                    json={"outcome": "approved"},
                )
            )
            == "SESSION_STATE_INVALID"
        )

        # a session nobody finished in time becomes expired the next time anyone looks, and its link stops
        v2 = w.create(h, customer_reference="late").json()

        async def lapse(column: str) -> None:
            async with engine(owner_url) as eng, eng.begin() as conn:
                await conn.execute(
                    text(
                        f"UPDATE verify.api_session SET {column} = now() - interval '1 minute' WHERE session_id = :s"
                    ),
                    {"s": v2["session_id"]},
                )

        asyncio.run(lapse("launch_expires_at"))
        assert (
            code(w.http.get(f"/v1/verify-api/hosted/{w.link_token(v2)}")) == "LAUNCH_INVALID"
        )  # the link's own half hour is up
        assert (
            w.http.get(
                f"/v1/verify-api/verification-sessions/{v2['session_id']}", headers=h
            ).json()["status"]
            == "ready"
        )
        asyncio.run(lapse("expires_at"))
        assert (
            w.http.get(
                f"/v1/verify-api/verification-sessions/{v2['session_id']}", headers=h
            ).json()["status"]
            == "expired"
        )
        assert (
            code(
                w.http.post(
                    f"/v1/verify-api/verification-sessions/{v2['session_id']}/resume-token",
                    headers=h,
                )
            )
            == "SESSION_STATE_INVALID"
        )
    finally:
        w.close()


def test_a_suspended_application_stops_creating_sessions_and_its_links_stop_opening(
    rt_url: str, owner_url: str
) -> None:
    w = World(rt_url)
    try:
        h = w.server("Pausable")
        v = w.create(h).json()
        assert w.http.get(f"/v1/verify-api/hosted/{w.link_token(v)}").status_code == 200

        async def pause() -> None:
            async with engine(rt_url) as eng, tenant_transaction(eng, ctx(w.tenant)) as t:
                app_id = (await service.list_applications(t))[0]["application_id"]
                for a in await service.list_applications(t):
                    if a["name"] == "Pausable":
                        app_id = a["application_id"]
                await service.set_status(t, app_id, "SUSPENDED")

        asyncio.run(pause())
        assert (
            code(w.http.get(f"/v1/verify-api/hosted/{w.link_token(v)}")) == "LAUNCH_INVALID"
        )  # a person is not sent to a company whose access was suspended
        assert w.create(h).status_code == 401  # and its server's token ended with it
    finally:
        w.close()


def test_guessing_launch_links_is_limited_and_the_catalogue_is_readable(
    rt_url: str, owner_url: str
) -> None:
    w = World(rt_url)
    try:
        codes = [w.http.get(f"/v1/verify-api/hosted/zvl_{i:043d}").status_code for i in range(12)]
        assert codes[:10] == [404] * 10 and codes[10:] == [429, 429]
        h = w.server()
        wf = w.http.get("/v1/verify-api/workflows", headers=h).json()
        assert {x["workflow_id"] for x in wf["workflows"]} == {
            "wf_document_selfie",
            "wf_document_only",
        } and "customer_onboarding" in wf["purposes"]
        pr = w.http.get("/v1/verify-api/products", headers=h).json()["items"]
        assert {p["method"] for p in pr} == {"document_authenticity", "face_match"}
        env = {p["method"]: p["environments"] for p in pr}
        assert env["document_authenticity"] == ["sandbox"]  # no live document authenticity check
        assert env["face_match"] == ["sandbox", "production"]  # the live face route serves it
    finally:
        w.close()


def test_a_signed_in_owner_lists_an_applications_sessions_and_makes_a_test_session_from_the_console(
    rt_url: str, owner_url: str
) -> None:
    from onboarding_fixture import onboard as onboard_owner

    sessions_api._hosted_limiter = (
        FailureLimiter()
    )  # wrong links in earlier tests must not count here
    o = onboard_owner(
        rt_url,
        extra_actions=("verify_api.application.manage", "verify_api.application.read"),
        extra_resources=("verify_api_application:*",),
    )
    app = create_app(settings(rt_url, verify_hosted_origin=ORIGIN), keyring=o.ring)
    with TestClient(app, base_url=ISS) as http:
        h = o.bearer(o.owner)
        a = http.post("/v1/verify-api/applications", headers=h, json={"name": "Lab app"}).json()
        aid = a["application_id"]
        assert (
            http.get(f"/v1/verify-api/applications/{aid}/sessions", headers=h).json()["items"] == []
        )
        made = http.post(f"/v1/verify-api/applications/{aid}/test-sessions", headers=h, json={})
        assert made.status_code == 201 and made.headers["cache-control"] == "no-store", made.text
        view = made.json()
        assert view["status"] == "ready" and view["customer_reference"].startswith("test-")
        assert view["launch_url"].startswith(f"{ORIGIN}/verify/session/zvl_")
        again = http.post(
            f"/v1/verify-api/applications/{aid}/test-sessions",
            headers=h,
            json={"customer_reference": "tester-1", "country": "GB"},
        ).json()
        assert again["session_id"] != view["session_id"] and again["country"] == "GB"
        listed = http.get(f"/v1/verify-api/applications/{aid}/sessions", headers=h).json()["items"]
        assert len(listed) == 2 and all("launch_url" not in x for x in listed)
        # the link works for the person, and the result shows in the console list
        token = view["launch_url"].rsplit("/", 1)[-1]
        assert (
            http.post(
                f"/v1/verify-api/hosted/{token}:simulate", json={"outcome": "approved"}
            ).status_code
            == 200
        )
        done = http.get(
            f"/v1/verify-api/applications/{aid}/sessions", headers=h, params={"status": "completed"}
        ).json()["items"]
        assert [x["session_id"] for x in done] == [view["session_id"]]
        # an unknown application, a suspended one, and a bad body
        assert (
            http.get(f"/v1/verify-api/applications/{uuid.uuid4()}/sessions", headers=h).status_code
            == 404
        )
        assert (
            http.post(
                f"/v1/verify-api/applications/{aid}/test-sessions",
                headers=h,
                json={"country": "usa"},
            ).status_code
            == 422
        )
        assert (
            http.post(
                f"/v1/verify-api/applications/{aid}/test-sessions", headers=h, json={"bogus": 1}
            ).status_code
            == 422
        )
        http.post(f"/v1/verify-api/applications/{aid}:suspend", headers=h)
        assert (
            http.post(
                f"/v1/verify-api/applications/{aid}/test-sessions", headers=h, json={}
            ).status_code
            == 401
        )
        # no sign-in at all
        assert http.get(f"/v1/verify-api/applications/{aid}/sessions").status_code == 401


def test_a_decision_or_an_ended_session_reaches_the_companys_webhook_endpoint_without_personal_data(
    rt_url: str, owner_url: str
) -> None:
    import json

    import httpx
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from test_webhooks_pg import Receiver, public_dns
    from zid_outbox import CloudEvent
    from zid_platform.modules.webhook.infrastructure.delivery import (
        deliver_due,
        register,
        route,
        verify,
    )

    w = World(rt_url)
    try:
        h = w.server()

        def fixture(name: str, ref: str) -> str:
            r = w.http.post(
                f"/v1/verify-api/sandbox/fixtures/{name}/sessions",
                headers={**h, "Idempotency-Key": uid()},
                json={"customer_reference": ref},
            )
            return str(r.json()["session"]["session_id"])

        # the person finishes on the hosted page (no signed-in company on that call)
        v = w.create(h, customer_reference="person-a").json()
        w.http.post(
            f"/v1/verify-api/hosted/{w.link_token(v)}:simulate", json={"outcome": "approved"}
        )
        # canceled by the partner's server
        c = w.create(h, customer_reference="person-b").json()["session_id"]
        w.http.post(f"/v1/verify-api/verification-sessions/{c}:cancel", headers=h)
        # a review, then resolved: version 2 supersedes version 1
        rev = fixture("review", "person-c")
        w.http.post(
            f"/v1/verify-api/sandbox/sessions/{rev}:simulate",
            headers=h,
            json={"outcome": "approved"},
        )
        gone = fixture("expired", "person-d")

        async def outbox() -> list[Any]:
            async with engine(owner_url) as eng, eng.begin() as conn:
                return list(
                    (
                        await conn.execute(
                            text(
                                "SELECT event_id, event_type, aggregate_id, aggregate_version, payload, cell_id, correlation_id, occurred_at "
                                "FROM events.outbox_event WHERE tenant_id = :t AND aggregate_type = 'verify_api_session' ORDER BY occurred_at, aggregate_version"
                            ),
                            {"t": uuid.UUID(w.tenant)},
                        )
                    ).all()
                )

        rows = asyncio.run(outbox())
        decided = "com.zoiko.id.verification.api.session_decided.v1"
        closed = "com.zoiko.id.verification.api.session_closed.v1"
        got = {(r.aggregate_id, r.aggregate_version): (r.event_type, r.payload) for r in rows}
        assert (v["session_id"], 1) in got and got[(v["session_id"], 1)][0] == decided
        d1 = got[(v["session_id"], 1)][1]
        assert (
            d1["outcome"] == "verified"
            and d1["status"] == "completed"
            and d1["customer_reference"] == "person-a"
        )
        assert (
            d1["sandbox"] is True
            and d1["decision_version"] == 1
            and d1["supersedes_decision_id"] is None
        )
        assert got[(c, 1)] == (closed, got[(c, 1)][1]) and got[(c, 1)][1]["status"] == "canceled"
        assert (
            got[(rev, 1)][1]["outcome"] == "review_required"
            and got[(rev, 1)][1]["status"] == "manual_review"
        )
        second = got[(rev, 2)][1]
        assert got[(rev, 2)][0] == decided and second["outcome"] == "verified"
        assert second["supersedes_decision_id"] == got[(rev, 1)][1]["decision_id"]
        assert got[(gone, 1)][0] == closed and got[(gone, 1)][1]["status"] == "expired"
        assert len(rows) == 5
        blob = json.dumps([r.payload for r in rows])
        for never in ("launch", "zvl_", "client_secret", "zps_", "country", "metadata"):
            assert never not in blob, never

        # one verified endpoint subscribed to both gets every one of them, once
        key = Ed25519PrivateKey.generate()
        rx = Receiver([])
        client = httpx.AsyncClient(transport=httpx.MockTransport(rx.handler))

        async def deliver() -> dict[str, int]:
            async with engine(rt_url) as eng, tenant_transaction(eng, ctx(w.tenant)) as conn:
                ep = await register(
                    conn,
                    tenant_id=w.tenant,
                    url="https://hooks.example.com/verify",
                    event_types=[decided, closed],
                    key_id="zid-whk-2026-10",
                    created_by=uid(),
                    resolve=public_dns,
                )
                assert await verify(conn, ep, client=client, resolve=public_dns, sign=key.sign)
                made = 0
                for r in rows:
                    ev = CloudEvent(
                        id=str(r.event_id),
                        type=r.event_type,
                        source="urn:zoiko:id:verify_api",
                        subject=f"verify_api_session/{r.aggregate_id}",
                        time=r.occurred_at,
                        tenantid=w.tenant,
                        cellid=r.cell_id,
                        aggregatetype="verify_api_session",
                        aggregateid=r.aggregate_id,
                        aggregateversion=r.aggregate_version,
                        data=r.payload,
                        correlationid=r.correlation_id or "c",
                    )
                    made += await route(conn, ev)
                assert made == 5
                later = datetime.now(UTC) + timedelta(seconds=2)
                total = 0
                for _ in range(
                    3
                ):  # an endpoint takes a few at a time; the rest go on the next pass
                    done = await deliver_due(
                        conn, client=client, resolve=public_dns, sign=key.sign, now=later
                    )
                    total += done.get("DELIVERED", 0)
                return {"DELIVERED": total}

        assert asyncio.run(deliver()) == {"DELIVERED": 5}
        bodies = [json.loads(x.content) for x in rx.seen]
        assert sorted(b["data"]["customer_reference"] for b in bodies) == [
            "person-a", "person-b", "person-c", "person-c", "person-d",
        ] + []  # fmt: skip
    finally:
        w.close()
