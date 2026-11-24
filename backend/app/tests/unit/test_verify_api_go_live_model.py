"""The rules for opening production for a Verify API partner (CR-153): the request, the automatic sandbox checks, the operator's confirmations, and the record of what was approved."""

from __future__ import annotations

from datetime import UTC, datetime
from itertools import pairwise

import pytest
from zid_platform.modules.verify_api.domain import go_live as g
from zid_platform.modules.verify_api.domain import model as m

GOOD = {
    "markets": ["gb", "US", "gb"],
    "methods": ["face_match", "document_authenticity"],
    "purpose_code": "customer_onboarding",
    "volume_band": "1k_10k",
    "use_case": "Verify new customers of our savings app.",
    "terms_version": g.TERMS_VERSION,
    "dpa_version": g.DPA_VERSION,
    "accept_terms": True,
    "accept_dpa": True,
}


def refused(fn, *a, **k) -> str:  # type: ignore[no-untyped-def]
    with pytest.raises(m.PartnerError) as e:
        fn(*a, **k)
    return e.value.code


def test_a_request_is_cleaned_sorted_and_stamped_with_the_current_terms() -> None:
    c = g.check_request(GOOD)
    assert c["markets"] == ["GB", "US"] and c["methods"] == ["document_authenticity", "face_match"]
    assert (
        c["terms_version"] == g.TERMS_VERSION
        and c["use_case"] == "Verify new customers of our savings app."
    )


def test_every_part_of_a_request_is_required_and_checked() -> None:
    bad = {
        "markets": [
            [],
            ["usa"],
            ["U1"],
            [5],
            ["US"] * 0,
            [f"{a}{b}" for a in "ABCDE" for b in "ABCDE"],
        ],
        "methods": [[], ["nope"], ["face_match", 5], "face_match"],
        "purpose_code": ["", "marketing", None],
        "volume_band": ["", "huge", None],
        "use_case": ["", "short", "x" * 501, None, 5],
    }
    for field, values in bad.items():
        for v in values:
            assert refused(g.check_request, {**GOOD, field: v}) == "VALIDATION_FAILED", (field, v)
    for field in ("markets", "methods", "purpose_code", "volume_band", "use_case"):
        body = {k: v for k, v in GOOD.items() if k != field}
        assert refused(g.check_request, body) == "VALIDATION_FAILED", field


def test_terms_must_be_the_current_version_and_actually_accepted() -> None:
    for over in (
        {"accept_terms": False},
        {"accept_terms": "yes"},
        {"terms_version": "2020-01"},
        {"accept_dpa": False},
        {"dpa_version": "old"},
    ):
        assert refused(g.check_request, {**GOOD, **over}) == "TERMS_NOT_ACCEPTED", over


def test_the_sandbox_checks_are_met_only_by_real_results() -> None:
    none = g.certification(g.SandboxFacts(0, 0, 0))
    assert not g.certified(none) and [c["id"] for c in none] == [
        "sandbox_approved",
        "sandbox_declined",
        "webhook_ready",
    ]
    assert all(c["help"] for c in none)
    assert not g.certified(g.certification(g.SandboxFacts(3, 0, 1)))  # never tried a refusal
    assert not g.certified(g.certification(g.SandboxFacts(1, 1, 0)))  # no way to receive results
    assert g.certified(g.certification(g.SandboxFacts(1, 1, 1)))


def test_a_closed_request_never_reopens_and_only_review_leads_to_approval() -> None:
    assert set(g.TRANSITIONS) == set(g.GoLiveStatus)
    for done in (g.GoLiveStatus.APPROVED, g.GoLiveStatus.DECLINED, g.GoLiveStatus.WITHDRAWN):
        for other in g.GoLiveStatus:
            assert refused(g.check_transition, done, other) == "GO_LIVE_STATE_INVALID"
    assert (
        refused(g.check_transition, g.GoLiveStatus.SUBMITTED, g.GoLiveStatus.APPROVED)
        == "GO_LIVE_STATE_INVALID"
    )
    for a, b in pairwise(
        [g.GoLiveStatus.SUBMITTED, g.GoLiveStatus.IN_REVIEW, g.GoLiveStatus.APPROVED]
    ):
        assert g.check_transition(a, b) is b


def test_approval_needs_every_confirmation_and_none_is_assumed() -> None:
    full = {k: True for k in g.ATTESTATIONS}
    assert g.check_attestations(full) == full
    for k in g.ATTESTATIONS:
        for v in (False, None, "yes", 1):
            assert refused(g.check_attestations, {**full, k: v}) == "ATTESTATION_MISSING", (k, v)
    assert refused(g.check_attestations, {}) == "ATTESTATION_MISSING"
    assert set(g.ATTESTATIONS) == {
        "company_verified",
        "terms_signed",
        "privacy_reviewed",
        "billing_ready",
        "provider_approved",
    }


def test_a_decline_needs_a_reason_and_notes_are_bounded() -> None:
    assert refused(g.check_note, "", required=True) == "NOTE_REQUIRED"
    assert refused(g.check_note, "ab", required=True) == "NOTE_REQUIRED"
    assert g.check_note("  Not ready.  ", required=True) == "Not ready."
    assert g.check_note(None, required=False) == ""
    assert refused(g.check_note, "x" * 1001, required=False) == "VALIDATION_FAILED"


def _record(**over):  # type: ignore[no-untyped-def]
    base = {
        "tenant_id": "t1", "request_id": "r1", "markets": ["US", "GB"], "methods": ["face_match"], "purpose_code": "customer_onboarding",
        "terms_version": "2026-11", "dpa_version": "2026-11", "certification_checks": g.certification(g.SandboxFacts(1, 1, 1)),
        "attestations": {k: True for k in g.ATTESTATIONS}, "reviewer_id": "op1", "approver_id": "op2",
        "approved_at": datetime(2026, 11, 24, tzinfo=UTC),
    }  # fmt: skip
    return g.manifest(**{**base, **over})


def test_the_approval_record_says_exactly_what_was_allowed_and_its_digest_detects_any_change() -> (
    None
):
    a = _record()
    assert (
        a["environment"] == "PRODUCTION"
        and a["markets"] == ["GB", "US"]
        and a["reviewed_by"] == "op1"
        and a["approved_by"] == "op2"
    )
    assert a["sandbox_certification"] == {
        "sandbox_approved": True,
        "sandbox_declined": True,
        "webhook_ready": True,
    }
    assert len(g.digest(a)) == 32 and g.digest(a) == g.digest(_record())
    # the order markets were given in does not change the record; anything else does
    assert g.digest(_record(markets=["GB", "US"])) == g.digest(a)
    for change in (
        {"markets": ["GB"]},
        {"methods": ["document_authenticity"]},
        {"purpose_code": "fraud_prevention"},
        {"approver_id": "op3"},
        {"tenant_id": "t2"},
    ):
        assert g.digest(_record(**change)) != g.digest(a), change
