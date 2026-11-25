"""The pure rules for the live check (CR-164): the truthful assurance, the fixed reasons, the outcome mapping and the eligibility check."""

from __future__ import annotations

from typing import Any

import pytest
from zid_platform.modules.verify_api.domain import live as lv
from zid_platform.modules.verify_api.domain.model import PartnerError


def code(fn: Any, *a: Any, **k: Any) -> str:
    with pytest.raises(PartnerError) as e:
        fn(*a, **k)
    return e.value.code


def test_the_assurance_and_the_check_say_exactly_what_ran_and_nothing_more() -> None:
    assert lv.LIVE_ASSURANCE == {"scheme": "zoikoid-proofing", "level": "FACE_MATCH_NO_LIVENESS"}
    for result in lv.Result:
        checks = lv.checks_for(result)
        assert [c["method"] for c in checks] == [
            "face_match"
        ]  # never a document authenticity or a liveness line
    assert lv.checks_for(lv.Result.VERIFIED)[0]["result"] == "pass"
    assert (
        lv.checks_for(lv.Result.INCONCLUSIVE)[0]["result"] == "inconclusive"
    )  # never "fail": a mismatch is not an accusation
    text = (lv.LIVE_POLICY_NOTE + " ".join(lv.PARTNER_REASONS)).lower()
    for claim in ("liveness_pass", "authentic", "fraud", "verified person"):
        assert claim not in text


def test_a_partner_sees_only_the_fixed_reasons_and_never_an_accusation() -> None:
    assert lv.partner_reasons(
        ["FACE_DOES_NOT_MATCH_DOCUMENT_PHOTO", "FACE_CONSENT_GIVEN", "ALTERNATE_ROUTE_REVIEW_ONLY"]
    ) == ["FACE_DOES_NOT_MATCH_DOCUMENT_PHOTO"]
    # what would read as an accusation is folded into a neutral reason, in a stable order
    assert lv.partner_reasons(
        ["DOCUMENT_MANIPULATION_INDICATED", "PRESENTATION_ATTACK_DETECTED"]
    ) == ["FACE_NOT_FOUND_OR_UNCLEAR", "DOCUMENT_UNREADABLE"]
    assert lv.partner_reasons(["MRZ_CHECK_DIGITS_FAILED", "DOCUMENT_READING_NOT_RELIABLE"]) == [
        "DOCUMENT_UNREADABLE"
    ]
    assert lv.partner_reasons(["DOCUMENT_EXPIRED_POLICY_REVIEW"]) == ["DOCUMENT_EXPIRED"]
    assert lv.partner_reasons([]) == []
    shown = {
        r
        for engine in (
            "DOCUMENT_MANIPULATION_INDICATED",
            "CAPTURE_INJECTION_RISK",
            "PRESENTATION_ATTACK_DETECTED",
            "CLONE_SUSPECTED",
        )
        for r in lv.partner_reasons([engine])
    }
    assert shown <= set(lv.PARTNER_REASONS)
    assert not any(
        "MANIPULAT" in r or "ATTACK" in r or "CLONE" in r or "INJECTION" in r for r in shown
    )


def test_only_an_approval_is_verified_and_a_failure_is_a_retake_then_inconclusive_never_not_verified() -> (
    None
):
    ok = lv.outcome_of("APPROVED", ["FACE_MATCHES_DOCUMENT_PHOTO_NO_LIVENESS"], attempts_left=3)
    assert ok.result is lv.Result.VERIFIED and ok.reasons == ("FACE_MATCHES_DOCUMENT_PHOTO",)
    for decision in ("INDETERMINATE", "REJECTED", "REVIEW_REQUIRED", "ABANDONED"):
        again = lv.outcome_of(decision, ["FACE_NOT_FOUND_OR_UNCLEAR"], attempts_left=2)
        assert again.result is lv.Result.RETRY and again.reasons == (
            "FACE_NOT_FOUND_OR_UNCLEAR",
        ), decision
        done = lv.outcome_of(decision, ["FACE_DOES_NOT_MATCH_DOCUMENT_PHOTO"], attempts_left=0)
        assert done.result is lv.Result.INCONCLUSIVE and done.reasons == (
            "FACE_DOES_NOT_MATCH_DOCUMENT_PHOTO",
            "ATTEMPTS_USED_UP",
        ), decision
    # a retake with nothing recognised still says something neutral
    assert lv.outcome_of("INDETERMINATE", ["SOMETHING_INTERNAL"], attempts_left=1).reasons == (
        "ENGINE_UNAVAILABLE",
    )
    # an exhausted case is final even if a count says tries remain
    assert (
        lv.outcome_of("INDETERMINATE", [], attempts_left=2, exhausted=True).result
        is lv.Result.INCONCLUSIVE
    )
    assert lv.attempts_left(1, 3) == 2 and lv.attempts_left(5, 3) == 0


def test_a_production_session_needs_the_live_workflow_a_named_check_and_a_named_country() -> None:
    ok: dict[str, Any] = {
        "country": "gb",
        "workflow_id": "wf_document_selfie",
        "grant_markets": ["GB", "US"],
        "grant_methods": ["face_match", "document_authenticity"],
    }
    lv.check_live_eligible(**ok)
    assert (
        code(lv.check_live_eligible, **{**ok, "workflow_id": "wf_document_only"})
        == "PRODUCT_NOT_ENTITLED"
    )
    assert (
        code(lv.check_live_eligible, **{**ok, "grant_methods": ["document_authenticity"]})
        == "PRODUCT_NOT_ENTITLED"
    )
    assert code(lv.check_live_eligible, **{**ok, "country": "FR"}) == "MARKET_NOT_ENABLED"
    assert code(lv.check_live_eligible, **{**ok, "grant_markets": []}) == "MARKET_NOT_ENABLED"
