"""The live check for a partner's customer (CR-164), against the real database and the real HTTP routes, with the photo and face services faked the way the identity tests fake them.

What these hold: a production session is refused BEFORE anything is created unless the grant names the country and the check AND the owners have published the face route and acknowledged its limits; the customer
must read the notice and agree before anything is photographed; only a match is `verified` and it carries the lowest grade by name; a face that does not match is a retake and then `inconclusive`, never `not_verified`;
a service problem spends no try; nothing is kept; and the identity claim an approved case leaves is ended at once.
"""

from __future__ import annotations

import asyncio
import base64
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from conftest import engine
from sqlalchemy import text
from test_idv_face_route_pg import similarity
from test_idv_media_pg import entry, photo
from test_idv_media_pg import fake_imaging as _fake_imaging  # noqa: F401  (autouse fixture)
from test_verify_api_go_live_pg import ALL, GOOD, SIGN, Company, code, operator_tx, uid
from zid_platform.modules.verification.application.capture_checks import FACE_ROUTE_ACK
from zid_platform.modules.verification.domain import idv_policy
from zid_platform.modules.verify_api.api import sessions as sessions_api
from zid_platform.modules.verify_api.application import approvals_service
from zid_platform.modules.verify_api.application import go_live_service as go_live
from zid_platform.modules.verify_api.application.throttle import FailureLimiter

REASON_MATCH = ["FACE_MATCHES_DOCUMENT_PHOTO"]


def face_policy(threshold: float = 0.363) -> dict[str, Any]:
    return entry(
        result_valid_days=30,
        alternates=[
            {
                "route_id": "WEB_FACE",
                "may_approve": True,
                "automatic": True,
                "face_match": True,
                "face_threshold": threshold,
            }
        ],
    )


def publish(co: Company, *, acknowledged: bool = True, threshold: float = 0.363) -> None:
    """What the owners do: publish a country's policy and acknowledge the face route's limits."""
    table = idv_policy.parse({"markets": {"GB": face_policy(threshold)}})
    state = co.http.app.state  # type: ignore[attr-defined]
    state.idv_policies = lambda: table
    state.idv_face_route_ack = FACE_ROUTE_ACK if acknowledged else None


def go_production(co: Company) -> dict[str, str]:
    """Take a company through the real gate: sandbox certification, an operator review and a different operator's approval, a production application, a second administrator's approval of its credential, and a token."""
    co.ready()
    rid = co.http.post("/v1/verify-api/go-live-requests", headers=co.h, json=GOOD).json()[
        "request_id"
    ]
    op1, op2 = uid(), uid()

    async def operate() -> None:
        async with operator_tx(co.rt_url) as c:
            await go_live.start_review(c, rid, op1)
        async with operator_tx(co.rt_url) as c:
            await go_live.approve(c, rid, op2, ALL, None, sign=SIGN)

    asyncio.run(operate())
    app = co.http.post(
        "/v1/verify-api/applications",
        headers=co.h,
        json={"name": "Live app", "environment": "PRODUCTION"},
    )
    assert app.status_code == 201, app.text
    app_id = app.json()["application_id"]
    ask = co.http.post(
        f"/v1/verify-api/applications/{app_id}/credential-approvals",
        headers=co.h,
        json={"action": "ISSUE"},
    ).json()["approval_id"]

    async def second_admin() -> None:
        async with co.tx() as c:
            await approvals_service.approve(c, ask, uid(), None)

    asyncio.run(second_admin())
    cred = co.http.post(
        f"/v1/verify-api/applications/{app_id}/credentials", headers=co.h, json={"approval_id": ask}
    ).json()
    tok = co.http.post(
        "/v1/verify-api/oauth2/token",
        json={"client_id": cred["client_id"], "client_secret": cred["client_secret"]},
    ).json()["access_token"]
    return {"Authorization": f"Bearer {tok}"}


def create(co: Company, bearer: dict[str, str], **over: Any) -> Any:
    body = {
        "customer_reference": "cust-1",
        "country": "GB",
        "purpose_code": "customer_onboarding",
        "workflow_id": "wf_document_selfie",
        **over,
    }
    return co.http.post(
        "/v1/verify-api/verification-sessions",
        headers={**bearer, "Idempotency-Key": uid()},
        json=body,
    )


class Customer:
    """The person on the hosted page: only the link."""

    def __init__(self, co: Company, view: dict[str, Any]) -> None:
        self.co, self.session_id = co, view["session_id"]
        self.base = "/v1/verify-api/hosted/" + view["launch_url"].rsplit("/", 1)[-1]

    def get(self, suffix: str = "") -> Any:
        return self.co.http.get(self.base + suffix)

    def post(self, suffix: str, body: dict[str, Any] | None = None) -> Any:
        return self.co.http.post(self.base + suffix, json=body or {})

    def notice(self, agree: bool = True) -> Any:
        version = self.post(":open").json()["notice_version"]
        return self.post("/consent", {"consented": agree, "notice_version": version})

    def capture(self, nonce: str) -> Any:
        return self.post(
            "/capture",
            {
                "nonce": nonce,
                "captured_at": datetime.now(UTC).isoformat(),
                "image_base64": base64.b64encode(photo()).decode(),
                "selfie_base64": base64.b64encode(photo()).decode(),
            },
        )

    def attempt(self) -> Any:
        nonce = self.post("/nonce").json()["nonce"]
        return self.capture(nonce)


def rows(owner_url: str, sql: str, **p: Any) -> list[Any]:
    async def run() -> list[Any]:
        async with engine(owner_url) as eng, eng.connect() as c:
            return list((await c.execute(text(sql), p)).all())

    return asyncio.run(run())


@pytest.fixture(autouse=True)
def fresh_limiter() -> None:
    sessions_api._hosted_limiter = (
        FailureLimiter()
    )  # failed links in other tests must not count here


def test_a_production_session_is_refused_before_anything_is_created_unless_every_gate_holds(
    rt_url: str, owner_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    similarity(monkeypatch, 0.9)
    co = Company(rt_url)
    try:
        bearer = go_production(co)
        # the owners have published nothing and acknowledged nothing
        nothing = create(co, bearer)
        assert nothing.status_code == 403 and code(nothing) == "MARKET_NOT_ENABLED"
        # a published policy is not enough without the acknowledgement that the route has no liveness test
        publish(co, acknowledged=False)
        assert code(create(co, bearer)) == "MARKET_NOT_ENABLED"
        publish(co)
        # a country the company's approval does not name
        assert code(create(co, bearer, country="FR")) == "MARKET_NOT_ENABLED"
        # a country the approval names but no policy serves (and the global policy is not accepted)
        assert code(create(co, bearer, country="US")) == "MARKET_NOT_ENABLED"
        # the workflow with no route that can produce a verified result
        assert code(create(co, bearer, workflow_id="wf_document_only")) == "PRODUCT_NOT_ENTITLED"
        # every gate holds
        ok = create(co, bearer)
        assert ok.status_code == 201, ok.text
        assert ok.json()["mode"] == "production" and ok.json()["launch_url"].startswith("https://")
        # only the one that was allowed exists
        listed = co.http.get("/v1/verify-api/verification-sessions", headers=bearer).json()
        assert len(listed["items"]) == 1
    finally:
        co.close()


def test_a_customer_reads_the_notice_agrees_is_photographed_and_a_match_is_verified_with_the_lowest_grade_named(
    rt_url: str, owner_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    similarity(monkeypatch, 0.6)
    co = Company(rt_url)
    try:
        bearer = go_production(co)
        publish(co)
        view = create(co, bearer).json()
        who = Customer(co, view)

        # what the page is told: a live check, the notice version to show, and the tries. Never the partner's reference, never a score.
        ctx = who.get().json()
        assert (
            ctx["live"] is True
            and ctx["available"] is True
            and ctx["sandbox"] is False
            and ctx["outcomes"] == []
        )
        assert ctx["notice_version"] == "verify-api-live:gb-web-1" and ctx["attempts_left"] == 3
        assert "cust-1" not in str(ctx) and view["session_id"] not in str(ctx)

        # the order cannot be skipped: no photograph before the notice, and no value before agreement
        assert code(who.post("/nonce")) == "SESSION_STATE_INVALID"
        assert code(who.capture("some-nonce-value")) == "SESSION_STATE_INVALID"
        # a notice other than the current one is refused, so what was shown is what was agreed to
        wrong = who.post("/consent", {"consented": True, "notice_version": "an-old-notice"})
        assert wrong.status_code == 422 and code(wrong) == "VALIDATION_FAILED"
        agreed = who.notice()
        assert agreed.status_code == 200 and agreed.json() == {"consented": True, "done": False}

        done = who.attempt()
        assert done.status_code == 200, done.text
        # the person is told only that the checking is done: no result, no score, no reason
        assert done.json() == {"done": True, "closed": False, "return_origin": None}

        decision = co.http.get(
            f"/v1/verify-api/verification-sessions/{view['session_id']}/decision", headers=bearer
        ).json()
        assert (
            decision["outcome"] == "verified"
            and decision["status"] == "completed"
            and decision["sandbox"] is False
        )
        assert decision["assurance"] == {
            "scheme": "zoikoid-proofing",
            "level": "FACE_MATCH_NO_LIVENESS",
        }
        assert decision["checks"] == [
            {"method": "face_match", "result": "pass"}
        ]  # the ONE check that ran
        assert decision["reason_codes"] == REASON_MATCH
        assert decision["evidence_receipt_id"] is None and decision["decision_version"] == 1
        # the assurance level says "no liveness" by name; nothing ELSE in the decision may claim or hint at a check that did not run, or at a score
        flat = str({k: v for k, v in decision.items() if k != "assurance"}).lower()
        for never in ("liveness", "authenticity", "0.6", "threshold", "similarity", "cosine"):
            assert never not in flat

        # the link is spent, and nothing is kept
        assert who.get().status_code == 404
        tenant = uuid.UUID(co.o.tenant)
        assert (
            rows(
                owner_url,
                "SELECT count(*) FROM verify.idv_case_media WHERE tenant_id = :t",
                t=tenant,
            )[0][0]
            == 0
        )
        # the identity claim an approved case records is about a pseudonym, not the company's own person, so it is ended at once
        assert [
            r[0]
            for r in rows(
                owner_url, "SELECT status FROM verify.idv_case_claim WHERE tenant_id = :t", t=tenant
            )
        ] == ["REVOKED"]
        # one usage fact at the start and one at the final decision, and the ledger agrees
        usage = co.http.get("/v1/verify-api/usage", headers=co.h).json()
        assert (
            usage["totals"]["production"]["verified"] == 1
            and usage["totals"]["production"]["sessions_started"] == 1
        )
        assert usage["reconciliation"]["reconciled"] is True
    finally:
        co.close()


def test_a_face_that_does_not_match_is_a_retake_and_then_inconclusive_never_not_verified(
    rt_url: str, owner_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    similarity(monkeypatch, 0.1)  # well below the line
    co = Company(rt_url)
    try:
        bearer = go_production(co)
        publish(co)
        view = create(co, bearer).json()
        who = Customer(co, view)
        assert who.notice().status_code == 200

        first = who.attempt().json()
        assert first == {
            "done": False,
            "retry": True,
            "reason": "FACE_DOES_NOT_MATCH_DOCUMENT_PHOTO",
            "attempts_left": 2,
        }
        assert (
            co.http.get(
                f"/v1/verify-api/verification-sessions/{view['session_id']}", headers=bearer
            ).json()["status"]
            == "action_required"
        )
        # the old one-time value is dead: the page must ask for a new one
        stale = who.post("/nonce").json()["nonce"]
        assert who.capture(stale).json()["attempts_left"] == 1
        assert (
            code(who.capture(stale)) == "SESSION_STATE_INVALID"
        )  # the same value twice, with no new one between

        last = who.attempt().json()
        assert last == {"done": True, "closed": True, "return_origin": None}

        decision = co.http.get(
            f"/v1/verify-api/verification-sessions/{view['session_id']}/decision", headers=bearer
        ).json()
        assert decision["outcome"] == "inconclusive" and decision["status"] == "inconclusive"
        assert decision["reason_codes"] == [
            "FACE_DOES_NOT_MATCH_DOCUMENT_PHOTO",
            "ATTEMPTS_USED_UP",
        ]
        assert decision["checks"] == [
            {"method": "face_match", "result": "inconclusive"}
        ]
        assert (
            decision["assurance"] is None
        )  # no grade is claimed for a check that did not confirm anything
        assert "not_verified" not in str(decision) and "fraud" not in str(decision).lower()
        assert who.get().status_code == 404  # the link ends with the session
        # a final, unconfirmed check is still a completed check for usage: one fact
        usage = co.http.get("/v1/verify-api/usage", headers=co.h).json()
        assert (
            usage["totals"]["production"]["sessions_started"] == 1
            and usage["totals"]["production"]["inconclusive"] == 1
        )
    finally:
        co.close()


def test_a_customer_who_declines_ends_the_session_with_nothing_photographed(
    rt_url: str, owner_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    similarity(monkeypatch, 0.9)
    co = Company(rt_url)
    try:
        bearer = go_production(co)
        publish(co)
        view = create(co, bearer).json()
        who = Customer(co, view)
        no = who.notice(agree=False)
        assert no.status_code == 200 and no.json() == {
            "consented": False,
            "done": True,
            "return_origin": None,
        }
        session = co.http.get(
            f"/v1/verify-api/verification-sessions/{view['session_id']}", headers=bearer
        ).json()
        assert session["status"] == "canceled"
        assert (
            co.http.get(
                f"/v1/verify-api/verification-sessions/{view['session_id']}/decision",
                headers=bearer,
            ).status_code
            == 404
        )  # no decision: nothing was checked
        assert (
            who.get().status_code == 404 and who.post("/nonce").status_code == 404
        )  # the link is spent
        assert (
            rows(
                owner_url,
                "SELECT count(*) FROM verify.idv_case_media WHERE tenant_id = :t",
                t=uuid.UUID(co.o.tenant),
            )[0][0]
            == 0
        )
    finally:
        co.close()


def test_a_service_problem_spends_no_try_and_the_same_step_can_be_repeated(
    rt_url: str, owner_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    co = Company(rt_url)
    try:
        bearer = go_production(co)
        publish(co)
        view = create(co, bearer).json()
        who = Customer(co, view)
        assert who.notice().status_code == 200
        nonce = who.post("/nonce").json()["nonce"]

        similarity(monkeypatch, 0.5, down=True)  # the face service is down
        down = who.capture(nonce)
        assert down.status_code == 503 and code(down) == "CAPTURE_UNAVAILABLE"
        # nothing changed: still waiting for photographs, still every try, and the same one-time value still works
        assert (
            co.http.get(
                f"/v1/verify-api/verification-sessions/{view['session_id']}", headers=bearer
            ).json()["status"]
            == "capture_started"
        )
        assert who.get().json()["attempts_left"] == 3

        similarity(monkeypatch, 0.9)  # it comes back
        ok = who.capture(nonce)
        assert ok.status_code == 200 and ok.json()["done"] is True
        assert (
            co.http.get(
                f"/v1/verify-api/verification-sessions/{view['session_id']}/decision",
                headers=bearer,
            ).json()["outcome"]
            == "verified"
        )
    finally:
        co.close()


def test_a_sandbox_session_has_no_photograph_steps_and_no_session_can_be_driven_by_another_companys_link(
    rt_url: str, owner_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    similarity(monkeypatch, 0.9)
    co = Company(rt_url)
    try:
        publish(co)
        server = co.sandbox_server()
        sandbox = co.http.post(
            "/v1/verify-api/verification-sessions",
            headers={**server, "Idempotency-Key": uid()},
            json={
                "customer_reference": "t-1",
                "country": "GB",
                "purpose_code": "customer_onboarding",
            },
        ).json()
        who = Customer(co, sandbox)
        assert who.get().json()["live"] is False
        for suffix, body in (
            ("/consent", {"consented": True, "notice_version": "x"}),
            ("/nonce", None),
        ):
            r = who.post(suffix, body)
            assert r.status_code == 403 and code(r) == "ENVIRONMENT_MISMATCH", suffix
        # a made-up link is the same single answer as ever
        assert (
            co.http.post("/v1/verify-api/hosted/zvl_" + "a" * 40 + "/nonce", json={}).status_code
            == 404
        )
    finally:
        co.close()
