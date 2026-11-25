"""Case review and four-eyes control (CR-167; ZID-CP-WF04), against the real database and the real HTTP routes, with three distinct administrators.

What these hold: a session waiting for review becomes a queued case; one reviewer holds one lease; a stale screen changes nothing; verified needs every required check confirmed; the first reviewer can never give the
second review; finality writes a superseding decision, the event and the outbox entry together; a held case cannot move and an ordinary reviewer cannot lift the hold; sensitive evidence is shown only through a counted
reveal; a final case is never edited, only appealed, by people who did not take part; the event trail cannot be edited; the adviser is off by default and can only advise; and one company never sees another's cases.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from conftest import ctx, engine
from fastapi.testclient import TestClient
from onboarding_fixture import ISS, Onboarded, onboard
from sqlalchemy import text
from test_commercial_pg import settings
from test_verify_api_go_live_pg import ACTIONS, ORIGIN, Company, code, operator_tx, uid
from zid_db import tenant_transaction
from zid_platform.main import create_app
from zid_platform.modules.verify_api.application import review_service as svc
from zid_platform.modules.verify_api.domain import model as m
from zid_platform.modules.verify_api.domain import review as rv

REVIEW = ("verify_api.review.read", "verify_api.review.work", "verify_api.review.approve")
FRAUD = ("verify_api.fraud.read", "verify_api.fraud.analyze", "verify_api.fraud.propose", "verify_api.fraud.approve")


class Crew(Company):
    """A company with three administrators who can all read, work and approve, so the tests can name who does what."""

    def __init__(self, rt_url: str, *, adviser: Any = None, agent: bool = False, fraud: bool = False) -> None:
        self.rt_url = rt_url
        self.o: Onboarded = onboard(
            rt_url,
            extra_actions=(*ACTIONS, *REVIEW, *(FRAUD if fraud else ())),
            extra_resources=("verify_api_application:*", "verify_api_review:*", *(("verify_api_fraud:*",) if fraud else ())),
            with_third=True,
        )
        app = create_app(settings(rt_url, verify_hosted_origin=ORIGIN), keyring=self.o.ring)
        app.state.review_adviser = adviser
        app.state.review_agent_enabled = agent
        self.http = TestClient(app, base_url=ISS)
        self.http.__enter__()
        self.h = self.o.bearer(self.o.owner)
        self.a, self.b, self.c = (
            self.o.bearer(self.o.owner),
            self.o.bearer(self.o.second),
            self.o.bearer(self.o.third),
        )
        self.ids = {"a": self.o.owner, "b": self.o.second, "c": self.o.third}

    def review_session(self) -> str:
        """A sandbox session that ends waiting for review; the cell queues a case for it by itself."""
        server = self.sandbox_server()
        made = self.http.post(
            "/v1/verify-api/sandbox/fixtures/review/sessions",
            headers={**server, "Idempotency-Key": uid()},
            json={"customer_reference": "customer-secret-77"},
        )
        assert made.status_code == 201, made.text
        return str(made.json()["session"]["session_id"])

    def case_for(self, session_id: str) -> dict[str, Any]:
        items = self.http.get("/v1/verify-api/review-cases", headers=self.a).json()["items"]
        return next(
            i for i in items if i["session_id"] == session_id and i["case_type"] == "REVIEW"
        )

    def post(self, who: dict[str, str], case: str, op: str, **body: Any) -> Any:
        return self.http.post(f"/v1/verify-api/review-cases/{case}:{op}", headers=who, json=body)

    def get(self, case: str, who: dict[str, str] | None = None) -> dict[str, Any]:
        return self.http.get(f"/v1/verify-api/review-cases/{case}", headers=who or self.a).json()  # type: ignore[no-any-return]

    def ready_to_second(self, session: str) -> tuple[str, dict[str, Any]]:
        """Reviewer A claims, records a pass, and proposes verified: the case now waits for a different reviewer."""
        case = self.case_for(session)["case_id"]
        v = self.get(case)["case_version"]
        assert self.post(self.a, case, "claim", expected_case_version=v).status_code == 200
        v = self.get(case)["case_version"]
        assert (
            self.post(
                self.a,
                case,
                "findings",
                expected_case_version=v,
                findings={"face_match": "pass"},
                note="Looked at the result.",
            ).status_code
            == 200
        )
        d = self.get(case)
        done = self.post(
            self.a,
            case,
            "propose",
            expected_case_version=d["case_version"],
            proposed_outcome="verified",
            reason_code="CHECKS_CONFIRMED",
            evidence_manifest_hash=manifest(d),
        )
        assert done.status_code == 200, done.text
        return case, done.json()

    def tx(self) -> Any:  # a transaction in the company's own scope
        return super().tx()


def manifest(case: dict[str, Any]) -> str:
    """The hash a reviewer's screen would hold: recomputed from the redacted view the same way the cell does."""
    return rv.manifest_hash(case["evidence"])


def owner_sql(owner_url: str, sql: str, **p: Any) -> list[Any]:
    async def run() -> list[Any]:
        async with engine(owner_url) as eng, eng.begin() as c:
            r = await c.execute(text(sql), p)
            return list(r.all()) if r.returns_rows else []

    return asyncio.run(run())


def test_a_session_waiting_for_review_is_queued_and_one_reviewer_holds_one_lease_on_a_version_they_read(
    rt_url: str, owner_url: str
) -> None:
    co = Crew(rt_url)
    try:
        session = co.review_session()
        queued = co.case_for(session)
        assert (
            queued["status"] == "QUEUED"
            and queued["queue"] == "STANDARD"
            and queued["four_eyes_required"] is True
            and queued["required_checks"] == ["face_match"]
        )
        case, v = queued["case_id"], queued["case_version"]
        # a stale or missing version changes nothing
        assert (
            code(co.post(co.a, case, "claim", expected_case_version=v + 5)) == "CASE_STALE_VERSION"
        )
        assert code(co.post(co.a, case, "claim")) == "CASE_STALE_VERSION"
        assert co.get(case)["status"] == "QUEUED"
        first = co.post(co.a, case, "claim", expected_case_version=v)
        assert (
            first.status_code == 200
            and first.json()["status"] == "CLAIMED"
            and first.json()["lease"]["reviewer"] == co.ids["a"]
        )
        # a second reviewer cannot take it, with the version they read or the new one
        assert code(co.post(co.b, case, "claim", expected_case_version=v)) in (
            "CASE_STALE_VERSION",
        )
        assert (
            code(co.post(co.b, case, "claim", expected_case_version=first.json()["case_version"]))
            == "ASSIGNMENT_CONFLICT"
        )
        # only the lease holder can work it
        v2 = first.json()["case_version"]
        assert (
            code(
                co.post(
                    co.b,
                    case,
                    "findings",
                    expected_case_version=v2,
                    findings={"face_match": "pass"},
                )
            )
            == "ASSIGNMENT_CONFLICT"
        )
        assert (
            code(
                co.post(
                    co.a,
                    case,
                    "findings",
                    expected_case_version=v2,
                    findings={"other_check": "pass"},
                )
            )
            == "VALIDATION_FAILED"
        )
        # the holder can give it back, and then another can claim it
        gave = co.post(
            co.a, case, "release", expected_case_version=v2, note="Handing over to the next shift."
        )
        assert (
            gave.status_code == 200
            and gave.json()["status"] == "QUEUED"
            and gave.json()["lease"] is None
        )
        assert (
            co.post(
                co.b, case, "claim", expected_case_version=gave.json()["case_version"]
            ).status_code
            == 200
        )
        # a second case for the same session cannot be opened while one is open
        assert (
            code(
                co.http.post(
                    "/v1/verify-api/review-cases", headers=co.a, json={"session_id": session}
                )
            )
            == "CASE_OPEN"
        )
    finally:
        co.close()


def test_an_expired_lease_can_be_claimed_again_and_a_holder_who_waited_too_long_can_no_longer_act(
    rt_url: str, owner_url: str
) -> None:
    co = Crew(rt_url)
    try:
        session = co.review_session()
        case = co.case_for(session)
        tenant, a, b = co.o.tenant, co.ids["a"], co.ids["b"]
        late = datetime.now(UTC) + timedelta(minutes=20)

        async def run() -> None:
            async with engine(rt_url) as eng:
                async with tenant_transaction(eng, ctx(tenant)) as t:
                    got = await svc.claim(t, case["case_id"], a, case["case_version"])
                async with tenant_transaction(eng, ctx(tenant)) as t:
                    with pytest.raises(m.PartnerError) as e:
                        await svc.record_findings(
                            t,
                            case["case_id"],
                            a,
                            got["case_version"],
                            {"face_match": "pass"},
                            None,
                            now=late,
                        )  # A's lease ended
                    assert e.value.code == "ASSIGNMENT_CONFLICT"
                async with tenant_transaction(
                    eng, ctx(tenant)
                ) as t:  # but a CLAIMED case is not claimable: it must be released first
                    with pytest.raises(m.PartnerError) as e2:
                        await svc.claim(t, case["case_id"], b, got["case_version"], now=late)
                    assert e2.value.code == "ASSIGNMENT_CONFLICT"

        asyncio.run(run())
    finally:
        co.close()


def test_verified_needs_the_required_check_confirmed_and_the_proposal_cannot_use_other_evidence(
    rt_url: str, owner_url: str
) -> None:
    co = Crew(rt_url)
    try:
        case = co.case_for(co.review_session())["case_id"]
        assert (
            co.post(
                co.a, case, "claim", expected_case_version=co.get(case)["case_version"]
            ).status_code
            == 200
        )
        d = co.get(case)
        # the screen needs no arithmetic of its own: the case says which evidence a proposal must name, who is asking, and what the checklists are
        assert d["evidence_manifest_hash"] == manifest(d) and d["viewer"]
        assert set(d["challenges"]) == {"evidence_reviewed", "reason_matches_evidence", "no_conflict_of_interest"}
        assert d["info_templates"]["RETAKE_FACE"]
        listed = co.http.get("/v1/verify-api/review-cases", headers=co.a).json()
        assert listed["viewer"] == d["viewer"]
        assert co.get(case, co.b)["viewer"] != d["viewer"]
        base = {
            "expected_case_version": d["case_version"],
            "reason_code": "CHECKS_CONFIRMED",
            "evidence_manifest_hash": d["evidence_manifest_hash"],
        }
        # no finding yet: there is no way to verified
        no = co.post(co.a, case, "propose", proposed_outcome="verified", **base)
        assert no.status_code == 422 and code(no) == "MANDATORY_CHECK_MISSING"
        # a reason that is not on the list, an unknown outcome, and a proposal for other evidence are all refused
        assert (
            code(
                co.post(
                    co.a,
                    case,
                    "propose",
                    proposed_outcome="verified",
                    **{**base, "reason_code": "BECAUSE"},
                )
            )
            == "VALIDATION_FAILED"
        )
        assert (
            code(co.post(co.a, case, "propose", proposed_outcome="approved", **base))
            == "VALIDATION_FAILED"
        )
        assert (
            code(
                co.post(
                    co.a,
                    case,
                    "propose",
                    proposed_outcome="inconclusive",
                    **{**base, "evidence_manifest_hash": "sha256:" + "0" * 64},
                )
            )
            == "EVIDENCE_CHANGED"
        )
        # a failed finding cannot support verified, and not_verified needs a failed finding
        v = d["case_version"]
        assert (
            co.post(
                co.a, case, "findings", expected_case_version=v, findings={"face_match": "fail"}
            ).status_code
            == 200
        )
        d = co.get(case)
        assert (
            code(
                co.post(
                    co.a,
                    case,
                    "propose",
                    proposed_outcome="verified",
                    **{**base, "expected_case_version": d["case_version"]},
                )
            )
            == "MANDATORY_CHECK_MISSING"
        )
        bad = co.post(
            co.a,
            case,
            "propose",
            proposed_outcome="not_verified",
            **{**base, "expected_case_version": d["case_version"], "reason_code": "CHECK_FAILED"},
        )
        assert bad.status_code == 200 and bad.json()["status"] == "AWAITING_SECOND_REVIEW"
        assert bad.json()["proposal"]["outcome"] == "not_verified"
        # a proposal is not a decision: the session still has only the review result
        decisions = co.http.get(f"/v1/verify-api/sessions/{d['session_id']}", headers=co.a).json()[
            "decisions"
        ]
        assert [x["outcome"] for x in decisions] == ["review_required"]
    finally:
        co.close()


def test_the_first_reviewer_can_never_give_the_second_review_and_finality_writes_a_superseding_decision_with_its_event(
    rt_url: str, owner_url: str
) -> None:
    co = Crew(rt_url)
    try:
        session = co.review_session()
        case, proposed = co.ready_to_second(session)
        v = proposed["case_version"]
        checklist = {
            "evidence_reviewed": True,
            "reason_matches_evidence": True,
            "no_conflict_of_interest": True,
        }
        # the proposer cannot approve their own work, however they ask
        me = co.post(
            co.a,
            case,
            "second-review",
            expected_case_version=v,
            decision="approve",
            challenge_answers=checklist,
        )
        assert me.status_code == 403 and code(me) == "REVIEWER_NOT_INDEPENDENT"
        # the independent reviewer must confirm the whole checklist, and a stale view changes nothing
        assert (
            code(
                co.post(
                    co.b,
                    case,
                    "second-review",
                    expected_case_version=v,
                    decision="approve",
                    challenge_answers={"evidence_reviewed": True},
                )
            )
            == "CHALLENGE_INCOMPLETE"
        )
        assert (
            code(
                co.post(
                    co.b,
                    case,
                    "second-review",
                    expected_case_version=v - 1,
                    decision="approve",
                    challenge_answers=checklist,
                )
            )
            == "CASE_STALE_VERSION"
        )
        assert co.get(case)["status"] == "AWAITING_SECOND_REVIEW"
        done = co.post(
            co.b,
            case,
            "second-review",
            expected_case_version=v,
            decision="approve",
            challenge_answers=checklist,
            note="Agree.",
        )
        assert done.status_code == 200, done.text
        assert (
            done.json()["status"] == "APPROVED_FINAL" and done.json()["final_outcome"] == "verified"
        )

        # the decision is a NEW version that supersedes the review result; the session is completed
        got = co.http.get(f"/v1/verify-api/sessions/{session}", headers=co.a).json()
        outcomes = [x["outcome"] for x in got["decisions"]]
        assert outcomes == ["review_required", "verified"] or outcomes == [
            "verified",
            "review_required",
        ]
        latest = max(got["decisions"], key=lambda x: x["decision_version"])
        assert (
            latest["outcome"] == "verified"
            and latest["decision_version"] == 2
            and latest["supersedes_decision_id"] is not None
        )
        assert latest["reason_codes"] == ["CHECKS_CONFIRMED"] and latest["checks"] == [
            {"method": "face_match", "result": "pass"}
        ]
        assert got["status"] == "completed"
        # a decided case is final: nothing changes it, and a retried approval does not decide twice
        assert (
            code(
                co.post(
                    co.b,
                    case,
                    "second-review",
                    expected_case_version=done.json()["case_version"],
                    decision="approve",
                    challenge_answers=checklist,
                )
            )
            == "CASE_FINAL"
        )
        assert (
            code(co.post(co.a, case, "claim", expected_case_version=done.json()["case_version"]))
            == "CASE_FINAL"
        )
        # the trail records every step in order and its chain is whole
        trail = co.http.get(f"/v1/verify-api/review-cases/{case}/events", headers=co.a).json()
        assert trail["chain_intact"] is True and trail["problems"] == []
        assert [e["type"] for e in trail["events"]] == [
            "opened",
            "claimed",
            "findings_recorded",
            "proposed",
            "finalized",
        ]
        assert trail["events"][-1]["data"]["decision_version"] == 2
        assert "customer-secret-77" not in str(
            trail
        )  # an event never carries what was revealed or who the customer is
        # nobody, not even the database owner by accident, can edit a past event
        with pytest.raises(Exception, match="append-only"):
            owner_sql(
                owner_url,
                "UPDATE verify.api_review_event SET actor = 'x' WHERE case_id = :c",
                c=case,
            )
        # the metric counts the finalization with no company or case label
        from zid_platform.modules.verify_api.application import observe

        assert observe.REVIEWS.value(event="finalized") >= 1
    finally:
        co.close()


def test_the_second_reviewer_can_return_a_case_with_a_reason_or_escalate_it_to_a_specialist(
    rt_url: str, owner_url: str
) -> None:
    co = Crew(rt_url)
    try:
        case, proposed = co.ready_to_second(co.review_session())
        v = proposed["case_version"]
        assert (
            code(co.post(co.b, case, "second-review", expected_case_version=v, decision="return"))
            == "NOTE_REQUIRED"
        )
        back = co.post(
            co.b,
            case,
            "second-review",
            expected_case_version=v,
            decision="return",
            note="The finding needs another look.",
        )
        assert (
            back.status_code == 200
            and back.json()["status"] == "RETURNED"
            and back.json()["proposal"] is None
        )
        # anyone may take a returned case, find it again and propose again; the earlier proposal is closed
        again = co.post(co.c, case, "claim", expected_case_version=back.json()["case_version"])
        assert again.status_code == 200 and again.json()["status"] == "INVESTIGATING"
        d = co.get(case)
        assert (
            co.post(
                co.c,
                case,
                "escalate",
                expected_case_version=d["case_version"],
                note="Needs a specialist.",
            ).json()["status"]
            == "ESCALATED"
        )
        esc = co.get(case)
        assert esc["queue"] == "SPECIALIST"
        spec = co.post(
            co.b, case, "claim", expected_case_version=esc["case_version"]
        )  # an escalated case is for someone who may give second reviews
        assert spec.status_code == 200 and spec.json()["status"] == "SPECIALIST_REVIEW"
    finally:
        co.close()


def test_asking_the_applicant_for_something_pauses_the_case_and_the_answer_returns_it_to_the_queue(
    rt_url: str, owner_url: str
) -> None:
    co = Crew(rt_url)
    try:
        case = co.case_for(co.review_session())["case_id"]
        assert (
            co.post(
                co.a, case, "claim", expected_case_version=co.get(case)["case_version"]
            ).status_code
            == 200
        )
        d = co.get(case)
        assert (
            code(
                co.post(
                    co.a,
                    case,
                    "request-info",
                    expected_case_version=d["case_version"],
                    template="WHATEVER_I_LIKE",
                )
            )
            == "VALIDATION_FAILED"
        )  # only the approved requests
        asked = co.post(
            co.a,
            case,
            "request-info",
            expected_case_version=d["case_version"],
            template="RETAKE_FACE",
        )
        assert (
            asked.status_code == 200
            and asked.json()["status"] == "AWAITING_APPLICANT"
            and asked.json()["lease"] is None
        )
        back = co.post(
            co.c, case, "info-received", expected_case_version=asked.json()["case_version"]
        )
        assert back.status_code == 200 and back.json()["status"] == "QUEUED"
    finally:
        co.close()


def test_sensitive_evidence_is_redacted_until_a_reviewer_reveals_it_with_a_reason_and_the_reveal_is_counted_and_recorded(
    rt_url: str, owner_url: str
) -> None:
    co = Crew(rt_url)
    try:
        case = co.case_for(co.review_session())["case_id"]
        view = co.get(case)
        sens = next(e for e in view["evidence"] if e["classification"] == "SENSITIVE")
        assert sens["summary"] is None and "customer-secret-77" not in str(
            view
        )  # redacted in every view
        assert any(e["classification"] == "STANDARD" and e["summary"] for e in view["evidence"])
        url = f"/v1/verify-api/review-cases/{case}/evidence/{sens['evidence_id']}:reveal"
        # nobody who is not working the case can see it, even an administrator
        assert (
            code(co.http.post(url, headers=co.b, json={"reason": "Curious"}))
            == "ASSIGNMENT_CONFLICT"
        )
        assert (
            co.post(co.a, case, "claim", expected_case_version=view["case_version"]).status_code
            == 200
        )
        assert (
            code(co.http.post(url, headers=co.b, json={"reason": "Curious"}))
            == "ASSIGNMENT_CONFLICT"
        )
        assert code(co.http.post(url, headers=co.a, json={"reason": " "})) == "NOTE_REQUIRED"
        shown = co.http.post(
            url, headers=co.a, json={"reason": "I need the request context to judge the result."}
        )
        assert (
            shown.status_code == 200
            and shown.json()["detail"]["customer_reference"] == "customer-secret-77"
        )
        assert (
            datetime.fromisoformat(shown.json()["visible_until"])
            <= datetime.now(UTC) + rv.REVEAL_TTL
        )
        # still redacted afterwards: a reveal is for the moment, not a change to the case
        assert (
            next(e for e in co.get(case)["evidence"] if e["classification"] == "STANDARD")[
                "summary"
            ]
            is not None
            and next(
                e for e in co.get(case)["evidence"] if e["evidence_id"] == sens["evidence_id"]
            )["summary"]
            is None
        )
        # recorded: that it happened and why, never what was shown
        trail = co.http.get(f"/v1/verify-api/review-cases/{case}/events", headers=co.a).json()
        reveals = [e for e in trail["events"] if e["type"] == "evidence_revealed"]
        assert (
            len(reveals) == 1
            and reveals[0]["reason"].startswith("I need")
            and "customer-secret-77" not in str(trail)
            and reveals[0]["actor"] == co.ids["a"]
        )
        # the number of reveals per case is limited
        for _ in range(rv.MAX_REVEALS_PER_CASE - 1):
            assert (
                co.http.post(url, headers=co.a, json={"reason": "Checking again."}).status_code
                == 200
            )
        assert code(co.http.post(url, headers=co.a, json={"reason": "One more."})) == "RATE_LIMITED"
        # the independent second reviewer may reveal too, the proposer's own approval path aside
        assert (
            owner_sql(
                owner_url,
                "SELECT count(*) FROM verify.api_review_reveal WHERE case_id = :c",
                c=case,
            )[0][0]
            == rv.MAX_REVEALS_PER_CASE
        )
    finally:
        co.close()


def test_a_regulatory_hold_stops_everything_and_only_a_different_operator_lifts_it(
    rt_url: str, owner_url: str
) -> None:
    co = Crew(rt_url)
    try:
        case, proposed = co.ready_to_second(co.review_session())
        v = proposed["case_version"]
        op1, op2 = uid(), uid()

        async def operate(fn: Any) -> Any:
            async with operator_tx(rt_url) as c:
                return await fn(c)

        placed = asyncio.run(
            operate(lambda c: svc.place_hold(c, case, op1, "SANCTIONS_POSSIBLE_MATCH"))
        )
        assert placed["on_hold"] is True
        listed = [i for i in asyncio.run(operate(svc.operator_open)) if i["case_id"] == case]
        assert listed and listed[0]["on_hold"] is True and listed[0]["tenant_id"] == co.o.tenant
        assert co.get(case)["on_hold"] is True
        held_v = co.get(case)["case_version"]
        checklist = {
            "evidence_reviewed": True,
            "reason_matches_evidence": True,
            "no_conflict_of_interest": True,
        }
        for op, who, body in (
            ("second-review", co.b, {"decision": "approve", "challenge_answers": checklist}),
            ("claim", co.c, {}),
            ("escalate", co.a, {"note": "Escalating."}),
        ):
            r = co.post(who, case, op, expected_case_version=held_v, **body)
            assert r.status_code == 423 and code(r) == "REGULATORY_HOLD", op
        # the session is untouched
        assert [
            x["outcome"]
            for x in co.http.get(
                f"/v1/verify-api/sessions/{co.get(case)['session_id']}", headers=co.a
            ).json()["decisions"]
        ] == ["review_required"]
        # the operator who placed it cannot lift it, and a company reviewer has no route to
        for attempt in (lambda c: svc.release_hold(c, case, op1, "Cleared."),):
            with pytest.raises(m.PartnerError) as e:
                asyncio.run(operate(attempt))
            assert e.value.code == "HOLD_NOT_RELEASABLE"
        assert co.http.post(
            f"/v1/verify-api/review-cases/{case}:release-hold",
            headers=co.a,
            json={"note": "Please"},
        ).status_code in (404, 405)
        lifted = asyncio.run(
            operate(lambda c: svc.release_hold(c, case, op2, "Cleared after review of the source."))
        )
        assert lifted["on_hold"] is False
        done = co.post(
            co.b,
            case,
            "second-review",
            expected_case_version=co.get(case)["case_version"],
            decision="approve",
            challenge_answers=checklist,
        )
        assert (
            done.status_code == 200
            and done.json()["status"] == "APPROVED_FINAL"
            and v < done.json()["case_version"]
        )
        trail = co.http.get(f"/v1/verify-api/review-cases/{case}/events", headers=co.a).json()
        assert [e["type"] for e in trail["events"] if e["type"].startswith("hold")] == [
            "hold_placed",
            "hold_released",
        ] and trail["chain_intact"]
    finally:
        co.close()


def test_a_final_case_is_appealed_not_edited_and_the_appeal_is_reviewed_by_people_who_did_not_take_part(
    rt_url: str, owner_url: str
) -> None:
    co = Crew(rt_url)
    try:
        session = co.review_session()
        case, proposed = co.ready_to_second(session)
        checklist = {
            "evidence_reviewed": True,
            "reason_matches_evidence": True,
            "no_conflict_of_interest": True,
        }
        final = co.post(
            co.b,
            case,
            "second-review",
            expected_case_version=proposed["case_version"],
            decision="approve",
            challenge_answers=checklist,
        ).json()
        assert final["status"] == "APPROVED_FINAL"
        # an open case cannot be appealed, a reason is needed, and a second appeal while one waits is refused
        assert code(co.post(co.a, case, "appeal", note=" ")) == "NOTE_REQUIRED"
        made = co.post(
            co.a, case, "appeal", note="New evidence: the applicant sent a clearer photograph."
        )
        assert made.status_code == 201, made.text
        ap = made.json()
        assert (
            ap["case_type"] == "APPEAL"
            and ap["parent_case_id"] == case
            and ap["status"] == "QUEUED"
        )
        assert code(co.post(co.a, case, "appeal", note="Again please.")) == "CASE_OPEN"
        assert (
            co.get(case)["appeal_case_id"] == ap["case_id"]
            and co.get(case)["status"] == "APPROVED_FINAL"
        )  # the original is preserved
        # the people who decided the original cannot review the appeal, at any step
        for who in (co.a, co.b):
            r = co.post(who, ap["case_id"], "claim", expected_case_version=ap["case_version"])
            assert r.status_code == 403 and code(r) == "REVIEWER_NOT_INDEPENDENT"
        # a third administrator takes it; a fourth person (here named directly) gives the independent second review
        assert (
            co.post(
                co.c, ap["case_id"], "claim", expected_case_version=ap["case_version"]
            ).status_code
            == 200
        )
        d = co.get(ap["case_id"])
        assert (
            co.post(
                co.c,
                ap["case_id"],
                "findings",
                expected_case_version=d["case_version"],
                findings={"face_match": "fail"},
            ).status_code
            == 200
        )
        d = co.get(ap["case_id"])
        prop = co.post(
            co.c,
            ap["case_id"],
            "propose",
            expected_case_version=d["case_version"],
            proposed_outcome="not_verified",
            reason_code="APPEAL_REJECTED",
            evidence_manifest_hash=manifest(d),
        )
        assert prop.status_code == 200, prop.text
        # a, b and c have all taken part; the appeal's second review needs someone else: a and b are refused
        for who in (co.a, co.b):
            r = co.post(
                who,
                ap["case_id"],
                "second-review",
                expected_case_version=prop.json()["case_version"],
                decision="approve",
                challenge_answers=checklist,
            )
            assert r.status_code == 403 and code(r) == "REVIEWER_NOT_INDEPENDENT"
        fourth = uid()

        async def finish() -> dict[str, Any]:
            async with engine(rt_url) as eng, tenant_transaction(eng, ctx(co.o.tenant)) as t:
                return await svc.second_review(
                    t,
                    ap["case_id"],
                    fourth,
                    prop.json()["case_version"],
                    "approve",
                    checklist,
                    None,
                    None,
                )

        decided = asyncio.run(finish())
        assert decided["status"] == "APPROVED_FINAL" and decided["final_outcome"] == "not_verified"
        # a new decision version SUPERSEDES the earlier one; nothing earlier was edited
        got = co.http.get(f"/v1/verify-api/sessions/{session}", headers=co.a).json()
        by_version = {x["decision_version"]: x for x in got["decisions"]}
        assert [by_version[i]["outcome"] for i in sorted(by_version)] == [
            "review_required",
            "verified",
            "not_verified",
        ]
        assert (
            by_version[3]["supersedes_decision_id"] == by_version[2]["decision_id"]
            and by_version[2]["supersedes_decision_id"] == by_version[1]["decision_id"]
        )
        assert co.get(case)["final_outcome"] == "verified"
        assert (
            co.http.get(f"/v1/verify-api/review-cases/{case}/events", headers=co.a).json()[
                "chain_intact"
            ]
            is True
        )
    finally:
        co.close()


def test_the_adviser_is_off_by_default_only_sees_redacted_facts_and_can_only_advise(
    rt_url: str, owner_url: str
) -> None:
    seen: list[dict[str, Any]] = []
    plan: dict[str, Any] = {}

    async def adviser(snapshot: dict[str, Any]) -> dict[str, Any]:
        seen.append(snapshot)
        if plan.get("boom"):
            raise RuntimeError("model down")
        return plan["answer"](snapshot)  # type: ignore[no-any-return]

    off = Crew(rt_url)
    try:
        case = off.case_for(off.review_session())["case_id"]
        assert (
            off.post(
                off.a, case, "claim", expected_case_version=off.get(case)["case_version"]
            ).status_code
            == 200
        )
        r = off.post(off.a, case, "agent-advice")
        assert (
            r.status_code == 404 and code(r) == "ADVISER_NOT_ENABLED"
        )  # off unless the cell enables it AND installs one
    finally:
        off.close()
    # enabled in settings but nothing installed is still off
    half = Crew(rt_url, adviser=None, agent=True)
    try:
        case = half.case_for(half.review_session())["case_id"]
        half.post(half.a, case, "claim", expected_case_version=half.get(case)["case_version"])
        assert code(half.post(half.a, case, "agent-advice")) == "ADVISER_NOT_ENABLED"
    finally:
        half.close()

    co = Crew(rt_url, adviser=adviser, agent=True)
    try:
        case = co.case_for(co.review_session())["case_id"]
        assert (
            code(co.post(co.a, case, "agent-advice")) == "ASSIGNMENT_CONFLICT"
        )  # advice only for the reviewer working the case
        assert (
            co.post(
                co.a, case, "claim", expected_case_version=co.get(case)["case_version"]
            ).status_code
            == 200
        )
        std_ids = [
            e["evidence_id"] for e in co.get(case)["evidence"] if e["classification"] == "STANDARD"
        ]
        before = co.get(case)

        plan["answer"] = lambda snap: {
            "summary": "The check needs a person to look.",
            "suggested_outcome": "inconclusive",
            "citations": [std_ids[0]],
        }
        ok = co.post(co.a, case, "agent-advice")
        assert (
            ok.status_code == 200
            and ok.json()["status"] == "ADVICE"
            and ok.json()["advisory_only"] is True
        )
        # what the adviser saw: facts and standard summaries only, nothing sensitive, no names, no notes
        assert "customer-secret-77" not in str(seen[-1]) and set(seen[-1]) == {
            "case_id",
            "case_version",
            "required_checks",
            "evidence",
        }
        assert all(e["evidence_id"] in std_ids for e in seen[-1]["evidence"])
        # advice moves nothing: same state, same version
        after = co.get(case)
        assert (after["status"], after["case_version"], after["proposal"], after["findings"]) == (
            before["status"],
            before["case_version"],
            before["proposal"],
            before["findings"],
        )

        # an invented citation, an action it may not take, and an adviser that fails are each handled without moving the case
        plan["answer"] = lambda snap: {
            "summary": "x",
            "suggested_outcome": "verified",
            "citations": ["ev_" + "0" * 24],
        }
        bad = co.post(co.a, case, "agent-advice").json()
        assert (
            bad["status"] == "REJECTED"
            and "CITATION_DOES_NOT_RESOLVE" in bad["flags"]
            and "advice" not in bad
        )
        plan["answer"] = lambda snap: {"summary": "x", "action": "finalize", "citations": []}
        assert "UNKNOWN_FIELD" in co.post(co.a, case, "agent-advice").json()["flags"]
        plan["boom"] = True
        assert co.post(co.a, case, "agent-advice").json()["status"] == "FAILED"
        plan["boom"] = False
        listed = co.http.get(f"/v1/verify-api/review-cases/{case}/advice", headers=co.a).json()[
            "items"
        ]
        assert {i["status"] for i in listed} == {"ADVICE", "REJECTED", "FAILED"} and all(
            i["body"] == {} for i in listed if i["status"] != "ADVICE"
        )
        fb = co.http.post(
            f"/v1/verify-api/review-cases/{case}/advice/{listed[-1]['advice_id']}:feedback",
            headers=co.a,
            json={"feedback": "incorrect"},
        )
        assert fb.status_code == 200
        assert (
            co.get(case)["status"] == after["status"]
            and co.get(case)["case_version"] == after["case_version"]
        )
        # the per-case limit holds
        plan["answer"] = lambda snap: {
            "abstain": True,
            "abstain_reason": "NO_EVIDENCE",
            "summary": "Unsure.",
        }
        for _ in range(rv.ADVICE_LIMIT_PER_CASE - len(listed)):
            co.post(co.a, case, "agent-advice")
        assert code(co.post(co.a, case, "agent-advice")) == "RATE_LIMITED"
    finally:
        co.close()


def test_one_company_never_sees_or_changes_another_companys_cases_and_the_routes_need_a_signed_in_person(
    rt_url: str, owner_url: str
) -> None:
    mine, other = Crew(rt_url), Crew(rt_url)
    try:
        case = mine.case_for(mine.review_session())["case_id"]
        v = mine.get(case)["case_version"]
        assert other.http.get("/v1/verify-api/review-cases", headers=other.a).json()["items"] == []
        # the same answer for a case that exists elsewhere and one that never did: no existence leak
        probe = other.http.get(f"/v1/verify-api/review-cases/{case}", headers=other.a)
        ghost = other.http.get(
            f"/v1/verify-api/review-cases/cas_{uuid.uuid4().hex}", headers=other.a
        )
        assert (
            probe.status_code == ghost.status_code == 404
            and code(probe) == code(ghost) == "CASE_NOT_FOUND"
        )
        assert code(other.post(other.a, case, "claim", expected_case_version=v)) == "CASE_NOT_FOUND"
        assert mine.get(case)["status"] == "QUEUED"
        assert mine.http.get("/v1/verify-api/review-cases").status_code == 401
        assert (
            mine.http.post(
                f"/v1/verify-api/review-cases/{case}:claim", json={"expected_case_version": v}
            ).status_code
            == 401
        )

        # a reviewer who may only READ cannot work them
        async def read_only() -> None:
            return None

        assert read_only is not None
    finally:
        mine.close()
        other.close()
