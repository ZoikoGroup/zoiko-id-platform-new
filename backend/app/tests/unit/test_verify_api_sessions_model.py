"""The Verify API's session rules (CR-150): the state machine, request validation, idempotency identity and the sandbox simulator."""

from __future__ import annotations

from itertools import pairwise

import pytest
from zid_platform.modules.verify_api.domain import model as m
from zid_platform.modules.verify_api.domain import sessions as s

GOOD = {
    "customer_reference": "customer-48201",
    "country": "US",
    "purpose_code": "customer_onboarding",
}


def refused(fn, *a, **k) -> str:  # type: ignore[no-untyped-def]
    with pytest.raises(m.PartnerError) as e:
        fn(*a, **k)
    return e.value.code


def test_a_final_state_never_reopens_and_every_state_has_a_rule() -> None:
    assert set(s.TRANSITIONS) == set(s.Status)
    for final in s.FINAL:
        assert s.TRANSITIONS[final] == frozenset()
        for other in s.Status:
            assert refused(s.check_transition, final, other) == "SESSION_STATE_INVALID"


def test_the_normal_path_goes_through_capture_and_cannot_skip_to_a_result() -> None:
    path = [
        s.Status.CREATED,
        s.Status.READY,
        s.Status.OPENED,
        s.Status.CAPTURE_STARTED,
        s.Status.SUBMITTED,
        s.Status.PROCESSING,
        s.Status.COMPLETED,
    ]
    for a, b in pairwise(path):
        assert s.check_transition(a, b) is b
    # a person who has only opened the link cannot be completed without capturing anything
    for target in (s.Status.COMPLETED, s.Status.INCONCLUSIVE, s.Status.MANUAL_REVIEW):
        assert refused(s.check_transition, s.Status.OPENED, target) == "SESSION_STATE_INVALID"
        assert refused(s.check_transition, s.Status.READY, target) == "SESSION_STATE_INVALID"
    assert (
        s.check_transition(s.Status.ACTION_REQUIRED, s.Status.CAPTURE_STARTED)
        is s.Status.CAPTURE_STARTED
    )  # a retry within the session
    for live in (s.Status.READY, s.Status.OPENED, s.Status.PROCESSING, s.Status.MANUAL_REVIEW):
        assert s.check_transition(live, s.Status.CANCELED) is s.Status.CANCELED


def test_the_sandbox_shortcut_is_narrow() -> None:
    for frm in (s.Status.READY, s.Status.OPENED):
        for to in (
            s.Status.COMPLETED,
            s.Status.INCONCLUSIVE,
            s.Status.MANUAL_REVIEW,
            s.Status.EXPIRED,
        ):
            assert s.check_simulated_transition(frm, to) is to
    assert (
        s.check_simulated_transition(s.Status.MANUAL_REVIEW, s.Status.COMPLETED)
        is s.Status.COMPLETED
    )  # a review resolved
    assert (
        refused(s.check_simulated_transition, s.Status.MANUAL_REVIEW, s.Status.MANUAL_REVIEW)
        == "SESSION_STATE_INVALID"
    )
    for done in s.FINAL:
        assert (
            refused(s.check_simulated_transition, done, s.Status.COMPLETED)
            == "SESSION_STATE_INVALID"
        )
    assert (
        refused(s.check_simulated_transition, s.Status.CAPTURE_STARTED, s.Status.COMPLETED)
        == "SESSION_STATE_INVALID"
    )
    assert (
        refused(s.check_simulated_transition, s.Status.READY, s.Status.CANCELED)
        == "SESSION_STATE_INVALID"
    )


def test_a_session_request_is_cleaned_and_checked() -> None:
    c = s.check_session_request({**GOOD, "return_origin": "HTTPS://App.Example.com/"})
    assert c["workflow_id"] == "wf_document_selfie" and c["workflow_version"] == s.CURRENT_VERSION
    assert (
        c["region"] == "us"
        and c["locale"] == "en-US"
        and c["metadata"] == {}
        and c["return_origin"] == "https://app.example.com"
    )
    assert (
        s.check_session_request({**GOOD, "region": "eu-central", "locale": "de"})["region"]
        == "eu-central"
    )
    bad = {
        "customer_reference": ["", " lead", "trail ", "x" * 129, "has\ttab", 5, None],
        "country": ["us", "USA", "", None, "U1"],
        "purpose_code": ["", "marketing", None, "customer onboarding"],
        "locale": ["EN", "en_US", "english", ""],
        "region": ["US", "a", "x" * 17, "bad region"],
    }
    for field, values in bad.items():
        for v in values:
            assert refused(s.check_session_request, {**GOOD, field: v}) == "VALIDATION_FAILED", (
                field,
                v,
            )
    assert (
        refused(s.check_session_request, {**GOOD, "workflow_id": "wf_nope"})
        == "WORKFLOW_VERSION_UNAVAILABLE"
    )
    assert (
        refused(s.check_session_request, {**GOOD, "workflow_version": "1999-01"})
        == "WORKFLOW_VERSION_UNAVAILABLE"
    )
    for meta in (
        [],
        {"a": 1},
        {f"k{i}": "v" for i in range(11)},
        {"k" * 41: "v"},
        {"k": "v" * 201},
    ):
        assert refused(s.check_session_request, {**GOOD, "metadata": meta}) == "VALIDATION_FAILED"
    assert refused(s.check_session_request, {**GOOD, "return_origin": 5}) == "VALIDATION_FAILED"
    assert (
        refused(s.check_session_request, {"country": "US", "purpose_code": "customer_onboarding"})
        == "VALIDATION_FAILED"
    )  # no reference


def test_a_reference_is_opaque_visible_text() -> None:
    for ok in (
        "a",
        "customer-48201",
        "A b c",
        "x" * 128,
        "ünï".encode("ascii", "ignore").decode() or "u",
    ):
        s.check_session_request({**GOOD, "customer_reference": ok})


def test_idempotency_keys_have_a_safe_shape() -> None:
    assert s.check_idempotency_key("order-2026-11-24:0001") == "order-2026-11-24:0001"
    for bad in (None, "", "short", "has space in it", "x" * 129, "emoji-😀-key-1"):
        assert refused(s.check_idempotency_key, bad) == "VALIDATION_FAILED", bad


def test_the_same_request_always_has_the_same_identity_and_any_change_changes_it() -> None:
    a = s.check_session_request(GOOD)
    assert s.request_hash(a) == s.request_hash(
        s.check_session_request(dict(reversed(list(GOOD.items()))))
    )
    assert len(s.request_hash(a)) == 32
    for change in (
        {"customer_reference": "other"},
        {"country": "GB"},
        {"purpose_code": "fraud_prevention"},
        {"locale": "fr"},
        {"metadata": {"a": "b"}},
        {"workflow_id": "wf_document_only"},
    ):
        assert s.request_hash(s.check_session_request({**GOOD, **change})) != s.request_hash(a), (
            change
        )


def test_ids_and_launch_tokens_are_unguessable_and_shaped() -> None:
    sid, did, tok = s.new_session_id(), s.new_decision_id(), s.new_launch_token()
    assert (
        sid.startswith("vfs_")
        and len(sid) == 36
        and did.startswith("dec_")
        and len(did) == 36
        and tok.startswith("zvl_")
        and len(tok) >= 40
    )
    assert len({s.new_launch_token() for _ in range(50)}) == 50 and s.new_session_id() != sid


def test_every_simulated_outcome_is_synthetic_and_matches_the_workflow() -> None:
    methods = s.WORKFLOWS["wf_document_selfie"]["methods"]
    status, outcome, checks, reasons = s.simulate(s.Fixture.APPROVED, methods)
    assert (
        (status, outcome, reasons) == (s.Status.COMPLETED, s.Outcome.VERIFIED, [])
        and [c["method"] for c in checks] == methods
        and {c["result"] for c in checks} == {"pass"}
    )
    status, outcome, checks, reasons = s.simulate(s.Fixture.DECLINED, methods)
    assert (
        (status, outcome) == (s.Status.COMPLETED, s.Outcome.NOT_VERIFIED)
        and checks[0]["result"] == "fail"
        and all(r.startswith("SIMULATED_") for r in reasons)
    )
    status, outcome, _, reasons = s.simulate(s.Fixture.REVIEW, methods)
    assert (status, outcome) == (s.Status.MANUAL_REVIEW, s.Outcome.REVIEW_REQUIRED) and reasons == [
        "SIMULATED_HUMAN_REVIEW_NEEDED"
    ]
    status, outcome, _, reasons = s.simulate(s.Fixture.INCONCLUSIVE, methods)
    assert (status, outcome) == (s.Status.INCONCLUSIVE, s.Outcome.INCONCLUSIVE) and reasons == [
        "SIMULATED_PROVIDER_UNAVAILABLE"
    ]
    assert s.simulate(s.Fixture.EXPIRED, methods) == (s.Status.EXPIRED, None, [], [])
    assert (
        s.check_fixture("APPROVED") is s.Fixture.APPROVED
        and refused(s.check_fixture, "verified") == "VALIDATION_FAILED"
    )
    assert len(s.simulate(s.Fixture.APPROVED, s.WORKFLOWS["wf_document_only"]["methods"])[2]) == 1


def test_the_catalogue_lists_only_versions_a_session_can_pin() -> None:
    for wf in s.WORKFLOWS.values():
        assert s.CURRENT_VERSION in wf["versions"] and wf["methods"]
    assert all("sandbox" in p["environments"] and "Sandbox only" in p["note"] for p in s.PRODUCTS)
