"""Appeals against a company-verification decision (WF02 K06, K10; acceptance T37)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from zid_platform.modules.organization.domain import appeal as a

NOW = datetime(2026, 11, 17, tzinfo=UTC)
D = a.Decision(3, "K2", NOW - timedelta(days=5), reviewers=("r1", "r2"))
WHY = "The registry extract we uploaded is current and shows the same registered office."


def code(fn, **kw) -> str:
    with pytest.raises(a.AppealError) as e:
        fn(**kw)
    return e.value.code


def open_args(**over):
    return {"decision": D, "existing_states": [], "now": NOW, "reason": WHY, **over}


def decide_args(**over):
    return {
        "decision": D,
        "opened_by": "applicant-agent",
        "applicant": "applicant",
        "decider": "d1",
        "outcome": a.State.UPHELD,
        "reason": WHY,
        **over,
    }


def test_an_appeal_may_be_opened_inside_the_window_against_a_decision_below_the_top_grade() -> None:
    a.may_open(**open_args())


def test_the_window_one_open_appeal_and_the_reason_are_enforced() -> None:
    assert code(a.may_open, **open_args(now=NOW + timedelta(days=40))) == "APPEAL_WINDOW_CLOSED"
    assert (
        code(a.may_open, **open_args(existing_states=[a.State.UPHELD, a.State.OPEN]))
        == "APPEAL_ALREADY_OPEN"
    )
    a.may_open(
        **open_args(existing_states=[a.State.UPHELD, a.State.ALLOWED, a.State.WITHDRAWN])
    )  # earlier appeals are decided: a new one may open
    assert code(a.may_open, **open_args(reason="no")) == "REASON_REQUIRED"
    assert (
        code(a.may_open, **open_args(decision=a.Decision(4, "K4", NOW, ()))) == "NOTHING_TO_APPEAL"
    )
    a.may_open(
        **open_args(window=timedelta(days=60), now=NOW + timedelta(days=40))
    )  # the market sets the window


def test_nobody_who_made_the_decision_or_is_a_party_decides_the_appeal() -> None:  # T37
    for who in ("r1", "r2"):
        assert code(a.may_decide, **decide_args(decider=who)) == "DECIDER_MADE_THE_DECISION", who
    for who in ("applicant", "applicant-agent"):
        assert code(a.may_decide, **decide_args(decider=who)) == "DECIDER_IS_A_PARTY", who
    a.may_decide(**decide_args())
    a.may_decide(**decide_args(outcome=a.State.ALLOWED))


def test_a_decision_needs_a_real_outcome_and_reasons() -> None:
    assert code(a.may_decide, **decide_args(outcome=a.State.OPEN)) == "OUTCOME_INVALID"
    assert code(a.may_decide, **decide_args(outcome=a.State.WITHDRAWN)) == "OUTCOME_INVALID"
    assert code(a.may_decide, **decide_args(reason="too short")) == "REASON_REQUIRED"


def test_the_applicant_is_told_a_safe_reason_that_names_no_threshold_or_detector() -> None:  # T37
    for state in (a.State.OPEN, a.State.UPHELD, a.State.ALLOWED):
        text = a.applicant_message(state).lower()
        assert all(
            w not in text for w in ("threshold", "score", "model", "fraud", "sanction", "rule")
        )
    assert "stands" in a.applicant_message(
        a.State.UPHELD
    ) and "assessed again" in a.applicant_message(a.State.ALLOWED)
