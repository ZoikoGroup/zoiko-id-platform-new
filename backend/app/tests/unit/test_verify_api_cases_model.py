"""The pure rules for a partner's disputes, appeals and erasure requests (CR-159)."""

from __future__ import annotations

from typing import Any

import pytest
from zid_platform.modules.verify_api.domain import cases as c
from zid_platform.modules.verify_api.domain.model import PartnerError


def code(fn: Any, *a: Any, **k: Any) -> str:
    with pytest.raises(PartnerError) as e:
        fn(*a, **k)
    return e.value.code


def test_a_case_is_a_dispute_an_appeal_or_an_erasure_and_nothing_else() -> None:
    assert c.check_kind("dispute") is c.CaseKind.DISPUTE
    assert c.check_kind("ERASURE") is c.CaseKind.ERASURE
    assert code(c.check_kind, "complaint") == "VALIDATION_FAILED"


def test_a_dispute_or_appeal_names_one_session_and_an_erasure_names_one_reference() -> None:
    c.check_target(c.CaseKind.DISPUTE, session_id="vfs_" + "a" * 32, reference=None)
    c.check_target(c.CaseKind.ERASURE, session_id=None, reference="customer-1")
    for kind, session, ref in (
        (c.CaseKind.DISPUTE, None, None),
        (c.CaseKind.APPEAL, "vfs_x", "customer-1"),
        (c.CaseKind.DISPUTE, None, "customer-1"),
        (c.CaseKind.ERASURE, None, None),
        (c.CaseKind.ERASURE, "vfs_x", "customer-1"),
        (c.CaseKind.ERASURE, "vfs_x", None),
    ):
        assert code(c.check_target, kind, session_id=session, reference=ref) == "VALIDATION_FAILED"


def test_only_a_session_with_a_result_can_be_disputed_and_only_a_non_pass_can_be_appealed() -> None:
    c.check_session_state(c.CaseKind.DISPUTE, "completed", "verified")
    c.check_session_state(c.CaseKind.APPEAL, "completed", "not_verified")
    c.check_session_state(c.CaseKind.APPEAL, "inconclusive", "inconclusive")
    assert code(c.check_session_state, c.CaseKind.DISPUTE, "opened", None) == "CASE_NOT_ALLOWED"
    assert (
        code(c.check_session_state, c.CaseKind.APPEAL, "completed", "verified")
        == "CASE_NOT_ALLOWED"
    )
    assert code(c.check_session_state, c.CaseKind.APPEAL, "completed", None) == "CASE_NOT_ALLOWED"


def test_a_reason_and_a_note_are_bounded_and_a_decision_needs_a_note() -> None:
    assert (
        c.check_reason("  The  applicant   passed in person. ") == "The applicant passed in person."
    )
    for bad in (None, "", "too short", "x" * 1001):
        assert code(c.check_reason, bad) == "VALIDATION_FAILED"
    assert c.check_note(None, required=False) == ""
    assert code(c.check_note, " ", required=True) == "NOTE_REQUIRED"
    assert code(c.check_note, "x" * 1001, required=False) == "VALIDATION_FAILED"
    assert c.check_note("Done.", required=True) == "Done."


def test_a_closed_case_never_reopens_and_only_review_leads_to_resolution() -> None:
    S = c.CaseStatus
    assert c.check_transition(S.OPEN, S.IN_REVIEW) is S.IN_REVIEW
    assert c.check_transition(S.IN_REVIEW, S.RESOLVED) is S.RESOLVED
    assert c.check_transition(S.OPEN, S.REJECTED) is S.REJECTED
    assert c.check_transition(S.IN_REVIEW, S.WITHDRAWN) is S.WITHDRAWN
    assert (
        code(c.check_transition, S.OPEN, S.RESOLVED) == "CASE_STATE_INVALID"
    )  # nobody resolves what nobody reviewed
    for done in (S.RESOLVED, S.REJECTED, S.WITHDRAWN):
        for target in S:
            assert code(c.check_transition, done, target) == "CASE_STATE_INVALID"


def test_a_reference_is_kept_only_as_a_hash_that_differs_between_companies() -> None:
    a = c.reference_hash("tenant-1", "customer-48201")
    assert len(a) == 32 and b"customer-48201" not in a
    assert a == c.reference_hash("tenant-1", "customer-48201")
    assert a != c.reference_hash(
        "tenant-2", "customer-48201"
    )  # the same reference at another company never matches
    assert a != c.reference_hash("tenant-1", "customer-48202")
    assert c.check_reference(" customer-1 ") == "customer-1"
    for bad in (None, "", "x" * 129):
        assert code(c.check_reference, bad) == "VALIDATION_FAILED"
