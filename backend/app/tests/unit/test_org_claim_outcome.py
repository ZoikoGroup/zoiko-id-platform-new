"""The one label of a company claim (WF-13B-03D): VERIFIED needs all four checks, and any other mix is never VERIFIED."""

from __future__ import annotations

import pytest
from zid_platform.modules.onboarding.api.org_claims import final_outcome

DONE = {
    "company": "COMPLETED",
    "person_assurance": "COMPLETED",
    "authority": "COMPLETED",
    "risk": "COMPLETED",
}


def test_all_four_checks_complete_is_verified() -> None:
    assert final_outcome(DONE) == "VERIFIED"


@pytest.mark.parametrize("check", list(DONE))
def test_one_check_not_complete_is_never_verified(check: str) -> None:
    assert final_outcome({**DONE, check: "PENDING"}) == "PROVISIONAL"
    assert final_outcome({**DONE, check: "NEEDS_ACTION"}) == "NEEDS_EVIDENCE"
    assert final_outcome({**DONE, check: "HELD"}) == "IN_REVIEW"
    assert final_outcome({**DONE, check: "DECLINED"}) == "DECLINED"


def test_the_strongest_problem_is_the_one_shown() -> None:
    """DECLINED, then RESTRICTED, IN_REVIEW, STALE, NEEDS_EVIDENCE: a mix of problems reads as the most serious one."""
    mixed = {
        "company": "STALE",
        "person_assurance": "PENDING",
        "authority": "NEEDS_ACTION",
        "risk": "RESTRICTED",
    }
    assert final_outcome(mixed) == "RESTRICTED"
    assert final_outcome({**mixed, "risk": "HELD"}) == "IN_REVIEW"
    assert final_outcome({**mixed, "risk": "COMPLETED"}) == "STALE"
    assert final_outcome({**mixed, "risk": "COMPLETED", "company": "COMPLETED"}) == "NEEDS_EVIDENCE"
    assert final_outcome({**mixed, "company": "DECLINED"}) == "DECLINED"


def test_a_lapsed_check_is_stale_and_a_suspended_screening_is_restricted_never_verified() -> None:
    from zid_platform.modules.onboarding.application.claims import _COMPANY, _SCREENING
    from zid_platform.modules.organization.ports import KybStatus
    from zid_platform.modules.screening.ports import ScreeningStatus

    assert _COMPANY[KybStatus.EXPIRED] == "STALE" and _SCREENING[ScreeningStatus.EXPIRED] == "STALE"
    assert _SCREENING[ScreeningStatus.SUSPENDED] == "RESTRICTED"
    assert final_outcome({**DONE, "company": _COMPANY[KybStatus.EXPIRED]}) == "STALE"
    assert final_outcome({**DONE, "risk": _SCREENING[ScreeningStatus.SUSPENDED]}) == "RESTRICTED"
    # nothing but COMPLETED on every check is VERIFIED: every other state, however friendly its name, is not
    for state in (
        "PENDING",
        "NEEDS_ACTION",
        "HELD",
        "STALE",
        "RESTRICTED",
        "DECLINED",
        "SOMETHING_NEW",
    ):
        assert final_outcome({**DONE, "authority": state}) != "VERIFIED"
