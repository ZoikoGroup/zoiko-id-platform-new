"""The pure rules of case review and four-eyes control (CR-167; ZID-CP-WF04): states, versions, leases, independence, the finality gate, the event chain, evidence redaction and the adviser's validation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from zid_platform.modules.verify_api.domain import review as r
from zid_platform.modules.verify_api.domain.model import PartnerError

NOW = datetime(2026, 11, 25, 10, 0, tzinfo=UTC)
REQ = ["face_match"]


def code(fn: Any, *a: Any, **k: Any) -> str:
    with pytest.raises(PartnerError) as e:
        fn(*a, **k)
    return e.value.code


def test_a_final_case_never_moves_and_the_ways_between_states_are_the_listed_ones() -> None:
    S = r.CaseState
    assert r.check_transition(S.QUEUED, S.CLAIMED) is S.CLAIMED
    assert r.check_transition(S.AWAITING_SECOND_REVIEW, S.APPROVED_FINAL) is S.APPROVED_FINAL
    assert r.check_transition(S.AWAITING_SECOND_REVIEW, S.RETURNED) is S.RETURNED
    assert (
        code(r.check_transition, S.QUEUED, S.APPROVED_FINAL) == "CASE_STATE_INVALID"
    )  # nobody finalizes what nobody reviewed
    assert code(r.check_transition, S.CLAIMED, S.RETURNED) == "CASE_STATE_INVALID"
    for target in S:
        assert code(r.check_transition, S.APPROVED_FINAL, target) == "CASE_STATE_INVALID"
    assert S.APPROVED_FINAL not in r.OPEN_STATES and set(r.TRANSITIONS) == set(S)


def test_a_change_must_name_the_version_it_read_and_the_lease_it_holds() -> None:
    r.check_version(4, 4)
    assert code(r.check_version, 4, 3) == "CASE_STALE_VERSION"
    assert (
        code(r.check_version, 4, None) == "CASE_STALE_VERSION"
    )  # a missing version is as wrong as an old one
    mine = r.Lease("l", "me", NOW + timedelta(minutes=5))
    r.require_lease(mine, "me", NOW)
    for bad, who in (
        (None, "me"),
        (mine, "someone-else"),
        (r.Lease("l", "me", NOW), "me"),  # expired the moment it is reached
        (r.Lease("l", "me", NOW + timedelta(minutes=5), released=True), "me"),
    ):
        assert code(r.require_lease, bad, who, NOW) == "ASSIGNMENT_CONFLICT"


def test_the_two_reviewers_are_never_the_same_principal() -> None:
    r.check_independent("a", "b")
    assert code(r.check_independent, "a", "a") == "REVIEWER_NOT_INDEPENDENT"


def test_verified_needs_every_required_check_confirmed_and_not_verified_needs_a_failed_one() -> (
    None
):
    assert (
        r.proposal_problems(r.Outcome.VERIFIED, required=REQ, findings={"face_match": "pass"}) == []
    )
    assert r.proposal_problems(r.Outcome.VERIFIED, required=REQ, findings={}) == [
        "MANDATORY_CHECK_MISSING:face_match"
    ]
    assert r.proposal_problems(
        r.Outcome.VERIFIED, required=["a", "b"], findings={"a": "pass", "b": "inconclusive"}
    ) == ["MANDATORY_CHECK_MISSING:b"]
    assert (
        r.proposal_problems(r.Outcome.NOT_VERIFIED, required=REQ, findings={"face_match": "fail"})
        == []
    )
    assert r.proposal_problems(
        r.Outcome.NOT_VERIFIED, required=REQ, findings={"face_match": "pass"}
    ) == ["NO_FAILED_CHECK_RECORDED"]
    assert r.proposal_problems(r.Outcome.INCONCLUSIVE, required=REQ, findings={}) == []
    assert r.check_findings({"face_match": "pass"}, REQ) == {"face_match": "pass"}
    assert code(r.check_findings, {"other": "pass"}, REQ) == "VALIDATION_FAILED"
    assert code(r.check_findings, {"face_match": "approved"}, REQ) == "VALIDATION_FAILED"


def gate(**over: Any) -> list[str]:
    base: dict[str, Any] = {
        "case_state": r.CaseState.AWAITING_SECOND_REVIEW, "case_version": 5, "proposal_case_version": 4, "proposal_manifest": "sha256:a", "current_manifest": "sha256:a", "on_hold": False,
        "four_eyes_required": True, "first_reviewer": "first", "approver": "second", "proposal_outcome": r.Outcome.VERIFIED, "required": REQ, "findings": {"face_match": "pass"},
    }  # fmt: skip
    return r.finality_problems(**{**base, **over})


def test_the_finality_gate_refuses_each_thing_that_must_stop_a_decision() -> None:
    assert gate() == []
    assert gate(on_hold=True) == ["REGULATORY_HOLD"]
    assert gate(current_manifest="sha256:b") == [
        "EVIDENCE_CHANGED"
    ]  # the evidence changed after the proposal was made
    assert gate(case_state=r.CaseState.APPROVED_FINAL) == ["CASE_ALREADY_FINAL"]
    assert gate(approver="first") == ["SECOND_REVIEWER_REQUIRED"]
    assert gate(approver=None) == ["SECOND_REVIEWER_REQUIRED"]
    assert gate(proposal_case_version=9) == ["PROPOSAL_FROM_THE_FUTURE"]
    assert gate(proposal_outcome=r.Outcome.REQUEST_INFORMATION) == ["NOT_A_FINAL_OUTCOME"]
    assert gate(findings={}) == [
        "MANDATORY_CHECK_MISSING:face_match"
    ]  # no incomplete route to verified, even with a second reviewer
    assert (
        gate(four_eyes_required=False, approver=None) == []
    )  # only an approved exemption lets one reviewer finish
    many = gate(on_hold=True, current_manifest="x", approver="first")
    assert {"REGULATORY_HOLD", "EVIDENCE_CHANGED", "SECOND_REVIEWER_REQUIRED"} <= set(
        many
    )  # every reason is named, not just the first


def test_the_evidence_manifest_names_exactly_what_the_case_held_whatever_order_it_is_listed_in() -> (
    None
):
    a = {"evidence_id": "ev_1", "kind": "check", "content_hash": "h1"}
    b = {"evidence_id": "ev_2", "kind": "decision", "content_hash": "h2"}
    assert r.manifest_hash([a, b]) == r.manifest_hash([b, a]) and r.manifest_hash(
        [a, b]
    ).startswith("sha256:")
    assert r.manifest_hash([a, b]) != r.manifest_hash([a, {**b, "content_hash": "h3"}])
    assert r.manifest_hash([a]) != r.manifest_hash([a, b])


def test_sensitive_evidence_is_redacted_until_revealed_and_restricted_evidence_never_is() -> None:
    std = {
        "evidence_id": "ev_1",
        "kind": "check",
        "label": "Check",
        "classification": r.STANDARD,
        "content_hash": "h",
        "summary": {"result": "review"},
    }
    sens = {
        **std,
        "evidence_id": "ev_2",
        "classification": r.SENSITIVE,
        "summary": {"customer": "x"},
    }
    assert r.redact(std)["summary"] == {"result": "review"}
    assert r.redact(sens)["summary"] is None and "detail" not in r.redact(sens)
    assert (
        r.check_reveal(sens, reason="Need the request context", reveals_so_far=0)
        == "Need the request context"
    )
    assert code(r.check_reveal, sens, reason="  ", reveals_so_far=0) == "NOTE_REQUIRED"
    assert code(r.check_reveal, None, reason="Because", reveals_so_far=0) == "NOT_FOUND"
    assert (
        code(
            r.check_reveal,
            {**sens, "classification": r.RESTRICTED},
            reason="Because",
            reveals_so_far=0,
        )
        == "EVIDENCE_RESTRICTED"
    )
    assert (
        code(r.check_reveal, sens, reason="Because", reveals_so_far=r.MAX_REVEALS_PER_CASE)
        == "RATE_LIMITED"
    )


def chain(n: int = 4) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    prev = r.GENESIS
    for i in range(1, n + 1):
        at = NOW + timedelta(seconds=i)
        d = r.event_digest(
            prev,
            seq=i,
            case_id="cas_1",
            event_type="step",
            actor="a",
            at=at,
            reason=None,
            data={"i": i},
        )
        out.append(
            {
                "case_id": "cas_1",
                "seq": i,
                "event_type": "step",
                "actor": "a",
                "reason": None,
                "data": {"i": i},
                "prev_digest": prev,
                "digest": d,
                "created_at": at,
            }
        )
        prev = d
    return out


def test_an_event_chain_shows_a_gap_a_reorder_or_an_edit() -> None:
    assert r.chain_problems(chain()) == []
    assert r.chain_problems([]) == []
    ev = chain()
    assert "GAP_AT_2" in r.chain_problems([ev[0], ev[2], ev[3]])
    edited = chain()
    edited[1] = {**edited[1], "actor": "someone-else"}
    assert "EDITED_AT_2" in r.chain_problems(edited)
    cut = chain()
    cut[2] = {**cut[2], "prev_digest": "f" * 64}
    assert "BROKEN_LINK_AT_3" in r.chain_problems(cut)


def test_the_adviser_abstains_when_it_should_and_its_answers_are_accepted_only_in_the_allowed_shape() -> (
    None
):
    ev = [{"kind": "check", "summary": {"result": "review"}}]
    assert r.adviser_must_abstain(evidence=ev, on_hold=False) is None
    assert r.adviser_must_abstain(evidence=ev, on_hold=True) == "REGULATORY_HOLD"
    assert r.adviser_must_abstain(evidence=[], on_hold=False) == "NO_EVIDENCE"
    assert (
        r.adviser_must_abstain(
            evidence=[{"kind": "check", "summary": {"result": "unavailable"}}], on_hold=False
        )
        == "SOURCE_UNAVAILABLE"
    )
    cites = ["ev_1", "ev_2"]
    good = {
        "summary": "The check needs a person.",
        "suggested_outcome": "inconclusive",
        "citations": ["ev_1"],
    }
    assert r.validate_advice(good, citable=cites).ok
    assert r.validate_advice(
        {"abstain": True, "abstain_reason": "NO_EVIDENCE", "summary": "I cannot tell."},
        citable=cites,
    ).ok
    for label, bad, flag in (
        ("not an object", "approve this", "NOT_AN_OBJECT"),
        ("an invented citation", {**good, "citations": ["ev_99"]}, "CITATION_DOES_NOT_RESOLVE"),
        (
            "a suggestion with no citation",
            {"summary": "x", "suggested_outcome": "verified", "citations": []},
            "SUGGESTION_WITHOUT_CITATION",
        ),
        ("an action it may not take", {**good, "action": "finalize"}, "UNKNOWN_FIELD"),
        ("an unknown suggestion", {**good, "suggested_outcome": "finalize_now"}, "BAD_SUGGESTION"),
        (
            "abstaining and suggesting",
            {"abstain": True, "suggested_outcome": "verified", "summary": "x"},
            "ABSTAINED_BUT_SUGGESTED",
        ),
        ("a huge summary", {**good, "summary": "x" * 5000}, "BAD_SUMMARY"),
        ("citations of the wrong kind", {**good, "citations": [1]}, "BAD_CITATIONS"),
    ):
        got = r.validate_advice(bad, citable=cites)
        assert not got.ok and flag in got.flags, label


def test_reason_codes_notes_and_outcomes_are_checked() -> None:
    assert r.check_reason_code("CHECKS_CONFIRMED") == "CHECKS_CONFIRMED"
    assert code(r.check_reason_code, "because I said so") == "VALIDATION_FAILED"
    assert (
        r.check_outcome("verified") is r.Outcome.VERIFIED
        and code(r.check_outcome, "approved") == "VALIDATION_FAILED"
    )
    assert (
        code(r.check_note, " ") == "NOTE_REQUIRED"
        and r.check_note(None, required=False) == ""
        and code(r.check_note, "x" * 1001) == "VALIDATION_FAILED"
    )
    assert set(r.INFO_TEMPLATES) and all(v for v in r.INFO_TEMPLATES.values())
