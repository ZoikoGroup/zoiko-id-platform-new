"""A provider that asks the person to send the evidence again (ZID-EDR-005): its own open state, never a decision, and the transitions around it."""

from __future__ import annotations

from datetime import timedelta

import pytest
from zid_platform.modules.verification.domain.verification import (
    Profile,
    ProviderResult,
    SessionStatus as S,
    VerificationError,
    decide,
    transition,
)


def _profile() -> Profile:
    return Profile(
        "p", "t", "n", 1, "IAL-Z2", ("legal_name",), frozenset({"GOVERNMENT_ID"}), True, True, False, frozenset({"GB"}), frozenset({"GB"}),
        timedelta(days=365), False, 1, frozenset({"onboarding"}), "ACTIVE",
    )  # fmt: skip


def _pending(*codes: str) -> ProviderResult:
    return ProviderResult("PENDING", (), None, None, (), tuple(codes))


def test_a_request_to_resubmit_is_its_own_open_state() -> None:
    d = decide(_profile(), _pending("RESUBMISSION_REQUIRED"), jurisdiction="GB")
    assert d.status is S.RESUBMISSION_REQUIRED and d.outcome == "RESUBMISSION_REQUIRED" and not d.verified_claims


def test_any_other_pending_result_stays_pending_external() -> None:
    for codes in ((), ("PROVIDER_MANUAL_REVIEW",), ("UNKNOWN_PROVIDER_STATUS",)):
        assert decide(_profile(), _pending(*codes), jurisdiction="GB").status is S.PENDING_EXTERNAL


def test_the_session_can_be_asked_again_and_can_still_end() -> None:
    for start in (S.IN_PROGRESS, S.PENDING_EXTERNAL):
        assert transition(start, S.RESUBMISSION_REQUIRED) is S.RESUBMISSION_REQUIRED
    for end in (S.IN_PROGRESS, S.PENDING_EXTERNAL, S.MANUAL_REVIEW, S.VERIFIED, S.FAILED, S.CANCELED, S.EXPIRED):
        assert transition(S.RESUBMISSION_REQUIRED, end) is end


def test_it_is_not_a_way_to_skip_the_start_and_it_is_not_final() -> None:
    with pytest.raises(VerificationError):
        transition(S.CREATED, S.RESUBMISSION_REQUIRED)
    from zid_platform.modules.verification.domain.verification import TERMINAL

    assert S.RESUBMISSION_REQUIRED not in TERMINAL
