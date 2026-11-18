"""WF-05 verification cases and evidence assertions (ZID-DTV-WF-05 §02, §06, §19, §21, A01, A09; CR-112). Synthetic only."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from zid_platform.modules.verification.domain import biometric_methods as bm
from zid_platform.modules.verification.domain import check_cases as cc
from zid_platform.modules.verification.domain import check_methods as cm
from zid_platform.modules.verification.domain.assertions import Assertion

NOW = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)
EMAIL = cm.CheckMethod.EMAIL_CHALLENGE
LOOKUP = cm.CheckMethod.ADDRESS_LOOKUP
DEVICE = cm.CheckMethod.DEVICE_RISK


def answer(method: cm.CheckMethod, available: bool, *reasons: str) -> cm.MethodEligibility:
    return cm.MethodEligibility(
        method, available, bm.State.PRODUCTION_ELIGIBLE if available else bm.State.OFF,
        tuple(reasons), cm.Route.OPEN, "e", "1", (),
    )  # fmt: skip


def case(**over: Any) -> cc.Case:
    base: dict[str, Any] = {
        "case_id": "c-1", "tenant_id": "t-1", "subject_ref": "cust-0042", "purpose": "ONBOARDING", "market": "GB", "region": "uk-1",
        "requested": [EMAIL, LOOKUP, DEVICE],
        "eligibility": [answer(EMAIL, True), answer(LOOKUP, True), answer(DEVICE, False, "NO_ADAPTER", "RETENTION_NOT_APPROVED:device.observation")],
        "policy_hash": "a" * 64, "correlation_id": "cor-1", "created_by": "admin", "now": NOW,
    }  # fmt: skip
    base.update(over)
    return cc.open_case(**base)


def rec(**over: Any) -> cc.AssertionRecord:
    base: dict[str, Any] = {
        "assertion_id": "a-1", "case_id": "c-1", "assertion": Assertion.CONTACT_CONTROL_CONFIRMED, "verdict": cc.Verdict.PASS,
        "method": EMAIL, "source": "zid-email-challenge@1", "reason_codes": (), "confidence": None, "observed_at": NOW,
        "expires_at": None, "recorded_by": "adapter", "recorded_at": NOW,
    }  # fmt: skip
    base.update(over)
    return cc.AssertionRecord(**base)


def refused(c: cc.Case, a: cc.AssertionRecord) -> str:
    with pytest.raises(cc.CaseError) as e:
        cc.check_assertion(c, a, now=NOW)
    return e.value.code


# ------------------------------------------------------------------------------------------------------------------- opening a case


def test_a_case_opens_eligible_with_only_the_methods_that_were_available_and_says_why_not_for_the_rest():
    c = case()
    assert c.status is cc.CaseStatus.ELIGIBLE and c.available == (EMAIL, LOOKUP)
    assert c.unavailable_reasons == (
        "DEVICE_RISK:NO_ADAPTER",
        "DEVICE_RISK:RETENTION_NOT_APPROVED:device.observation",
    )


def test_a_case_with_nothing_available_opens_created_and_never_opts_anyone_in_silently():
    """A01: if no lawful basis, region or approved source exists, the step is unavailable."""
    c = case(requested=[DEVICE])
    assert c.status is cc.CaseStatus.CREATED and c.available == ()
    assert c.unavailable_reasons[0] == "DEVICE_RISK:NO_ADAPTER"
    assert case(requested=[cm.CheckMethod.PHONE_SMS_CHALLENGE]).unavailable_reasons == (
        "PHONE_SMS_CHALLENGE:NOT_ASSESSED",
    )


@pytest.mark.parametrize(
    ("over", "code"),
    [
        ({"subject_ref": "dana@example.com"}, "SUBJECT_REF_INVALID"),
        ({"subject_ref": "+447700900123"}, "SUBJECT_REF_INVALID"),
        ({"subject_ref": "Dana Lee"}, "SUBJECT_REF_INVALID"),
        ({"purpose": "onboarding"}, "PURPOSE_INVALID"),
        ({"market": "GBR"}, "MARKET_INVALID"),
        ({"region": " "}, "REGION_REQUIRED"),
        ({"requested": []}, "METHODS_REQUIRED"),
        ({"requested": [EMAIL, EMAIL]}, "METHODS_DUPLICATED"),
        ({"now": datetime(2026, 11, 17, 12, 0)}, "TIME_INVALID"),
    ],
)
def test_a_case_is_bound_to_an_opaque_subject_a_purpose_a_market_and_a_region(over, code):
    with pytest.raises(cc.CaseError) as e:
        case(**over)
    assert e.value.code == code


def test_a_case_moves_only_along_the_wf05_states_and_closed_is_final():
    c = case()
    started = cc.move(c, cc.CaseStatus.IN_PROGRESS, now=NOW)
    reviewed = cc.move(started, cc.CaseStatus.REVIEW, now=NOW)
    done = cc.move(
        cc.move(reviewed, cc.CaseStatus.IN_PROGRESS, now=NOW), cc.CaseStatus.COMPLETE, now=NOW
    )
    closed = cc.move(done, cc.CaseStatus.CLOSED, now=NOW)
    for frm, to in (
        (c, cc.CaseStatus.COMPLETE),
        (closed, cc.CaseStatus.IN_PROGRESS),
        (case(requested=[DEVICE]), cc.CaseStatus.ELIGIBLE),  # its methods are fixed when it opens
    ):
        with pytest.raises(cc.CaseError) as e:
            cc.move(frm, to, now=NOW)
        assert e.value.code == "CASE_MOVE_NOT_ALLOWED"
    assert cc.MOVES[cc.CaseStatus.CLOSED] == frozenset()


# ------------------------------------------------------------------------------------------------------------------- assertions


def test_a_contact_challenge_pass_from_a_cleared_method_is_accepted():
    cc.check_assertion(case(), rec(), now=NOW)


def test_an_assertion_the_method_cannot_show_is_refused_so_nothing_is_promoted():
    assert (
        refused(case(), rec(assertion=Assertion.ADDRESS_ASSOCIATION_CONFIRMED))
        == "ASSERTION_NOT_PRODUCED_BY_METHOD"
    )
    lookup = rec(
        method=LOOKUP, assertion=Assertion.ADDRESS_DOCUMENT_REVIEWED, source="libpostal@1.1"
    )
    assert refused(case(), lookup) == "ASSERTION_NOT_PRODUCED_BY_METHOD"


def test_evidence_from_a_method_the_case_was_not_cleared_for_is_refused():
    device = rec(
        method=DEVICE,
        assertion=Assertion.DEVICE_RISK_OBSERVED,
        verdict=cc.Verdict.REVIEW,
        reason_codes=("EMULATOR",),
    )
    assert refused(case(), device) == "METHOD_NOT_AVAILABLE_FOR_CASE"


def test_a_pass_or_fail_names_its_source_and_anything_but_a_pass_names_a_reason():
    assert refused(case(), rec(source=None)) == "SOURCE_REQUIRED"
    assert (
        refused(
            case(), rec(verdict=cc.Verdict.FAIL, source=None, reason_codes=("CHALLENGE_EXPIRED",))
        )
        == "SOURCE_REQUIRED"
    )
    assert refused(case(), rec(verdict=cc.Verdict.UNAVAILABLE, source=None)) == "REASON_REQUIRED"
    cc.check_assertion(
        case(),
        rec(verdict=cc.Verdict.UNAVAILABLE, source=None, reason_codes=("DATA_SOURCE_UNAVAILABLE",)),
        now=NOW,
    )


def test_could_not_confirm_is_never_called_fraud():
    """WF-05 §21, A09."""
    for code in ("FRAUD", "SUSPECTED_FRAUD", "FRAUD_RING"):
        bad = rec(verdict=cc.Verdict.FAIL, reason_codes=(code,))
        assert refused(case(), bad) == "REASON_CODE_NOT_ALLOWED"
    assert (
        refused(case(), rec(verdict=cc.Verdict.FAIL, reason_codes=("lower case",)))
        == "REASON_CODE_INVALID"
    )


def test_a_closed_case_takes_no_new_evidence_and_times_and_expiry_are_checked():
    closed = cc.move(case(), cc.CaseStatus.CLOSED, now=NOW)
    assert refused(closed, rec()) == "CASE_CLOSED"
    assert refused(case(), rec(observed_at=NOW + timedelta(hours=1))) == "FROM_THE_FUTURE"
    assert refused(case(), rec(expires_at=NOW - timedelta(seconds=1))) == "EXPIRY_INVALID"
    assert refused(case(), rec(expires_at=NOW + timedelta(days=4000))) == "EXPIRY_INVALID"
    assert refused(case(), rec(case_id="c-2")) == "ASSERTION_CASE_MISMATCH"


# ------------------------------------------------------------------------------------------------------------------- the evidence view


def test_the_evidence_view_shows_each_assertion_on_its_own_and_never_an_overall_result():
    c = case()
    records = [
        rec(),
        rec(
            assertion_id="a-2", method=LOOKUP, assertion=Assertion.ADDRESS_LOCATION_MATCH, source="os-open-uprn@2026",
            verdict=cc.Verdict.PASS,
        ),
        rec(
            assertion_id="a-3", method=LOOKUP, assertion=Assertion.ADDRESS_FORMAT_VALID, source="libpostal@1.1",
            verdict=cc.Verdict.REVIEW, reason_codes=("POSTCODE_NOT_CHECKED",),
        ),
    ]  # fmt: skip
    view = cc.evidence_view(c, records, now=NOW)
    kinds = {a["type"]: a for a in view["assertions"]}
    assert set(kinds) == {
        "CONTACT_CONTROL_CONFIRMED",
        "ADDRESS_LOCATION_MATCH",
        "ADDRESS_FORMAT_VALID",
    }
    assert (
        kinds["ADDRESS_LOCATION_MATCH"]["does_not_prove"]
        == "That the person lives at it or owns it."
    )
    assert kinds["ADDRESS_FORMAT_VALID"]["outcome"] == "REVIEW"
    flat = repr(view).lower()
    for word in ("overall", 'verified"', "score", "green"):
        assert word not in flat.replace("no overall verified result", "")
    assert view["not_yet_shown"] == []


def test_a_later_assertion_supersedes_for_display_and_an_expired_one_is_never_a_pass():
    first = rec(expires_at=NOW + timedelta(days=30))
    later = replace(
        first,
        assertion_id="a-9",
        recorded_at=NOW + timedelta(minutes=1),
        verdict=cc.Verdict.FAIL,
        reason_codes=("CHALLENGE_EXPIRED",),
    )
    view = cc.evidence_view(case(), [later, first], now=NOW + timedelta(minutes=2))
    only = view["assertions"][0]
    assert only["outcome"] == "FAIL" and only["earlier_records"] == 1
    expired = cc.evidence_view(case(), [first], now=NOW + timedelta(days=31))["assertions"][0]
    assert expired["outcome"] == "EXPIRED" and "EVIDENCE_EXPIRED" in expired["reason_codes"]


def test_the_view_lists_what_the_cleared_methods_could_still_show():
    view = cc.evidence_view(case(), [rec()], now=NOW)
    assert view["not_yet_shown"] == ["ADDRESS_FORMAT_VALID", "ADDRESS_LOCATION_MATCH"]
