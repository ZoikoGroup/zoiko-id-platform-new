"""Appeals against a company-verification decision (ZID-DTV-WF-02 K06, K10; acceptance T37). Pure domain, no I/O.

An applicant who disagrees with where their company stands can ask for it to be looked at again, and the platform owes them a fair, separate look:

- only the applicant's own company can appeal, and only against a decision that is not an approval, within the window the market allows (a missed window is a new application, not an appeal);
- there is one open appeal for one decision; a second is refused until the first is decided;
- the person who decides is none of the reviewers who made the decision, not the applicant, and not whoever opened the appeal on their behalf: an appeal is never marked by the people whose work it questions;
- the outcome is UPHELD (the decision stands) or ALLOWED (the decision is set aside and the company is reassessed). Both give reasons. Neither edits the past: an allowed appeal adds a new assessment;
- the applicant is told a safe reason and what they can do next, never the detector thresholds or another party's data.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

DEFAULT_WINDOW = timedelta(days=30)
MIN_REASON = 20


class State(StrEnum):
    OPEN = "OPEN"
    UPHELD = "UPHELD"
    ALLOWED = "ALLOWED"
    WITHDRAWN = "WITHDRAWN"


class AppealError(Exception):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True, slots=True)
class Decision:
    """What an appeal is against: one assessment version, its grade and who made it."""

    version: int
    grade: str  # K0..K4
    decided_at: datetime
    reviewers: tuple[str, ...] = ()


def may_open(
    *,
    decision: Decision,
    existing_states: Iterable[State],
    now: datetime,
    window: timedelta = DEFAULT_WINDOW,
    reason: str,
) -> None:
    """Raises AppealError when this appeal may not be opened."""
    if decision.grade == "K4":
        raise AppealError("NOTHING_TO_APPEAL", "the company already holds the highest grade")
    if now - decision.decided_at > window:
        raise AppealError("APPEAL_WINDOW_CLOSED", "reapply with new evidence instead")
    if any(s is State.OPEN for s in existing_states):
        raise AppealError("APPEAL_ALREADY_OPEN", "decide the open appeal first")
    if len(reason.strip()) < MIN_REASON:
        raise AppealError(
            "REASON_REQUIRED",
            f"say in at least {MIN_REASON} characters what is wrong or what is new",
        )


def may_decide(
    *, decision: Decision, opened_by: str, applicant: str, decider: str, outcome: State, reason: str
) -> None:
    """Raises AppealError when this person may not decide this appeal this way."""
    if outcome not in (State.UPHELD, State.ALLOWED):
        raise AppealError("OUTCOME_INVALID", outcome.value)
    if len(reason.strip()) < MIN_REASON:
        raise AppealError(
            "REASON_REQUIRED",
            f"a decision on an appeal gives its reasons in at least {MIN_REASON} characters",
        )
    if decider in decision.reviewers:
        raise AppealError("DECIDER_MADE_THE_DECISION", "someone else looks at an appeal")
    if decider in (applicant, opened_by):
        raise AppealError(
            "DECIDER_IS_A_PARTY", "the applicant and whoever opened the appeal do not decide it"
        )


SAFE_REASON = {
    State.UPHELD: "We looked again, with someone who was not involved the first time, and the decision stands. You can add evidence and apply again.",
    State.ALLOWED: "We looked again and set the earlier decision aside. Your company is being assessed again.",
}


def applicant_message(outcome: State) -> str:
    """The wording the applicant sees. It names no threshold, rule, detector or other party."""
    return SAFE_REASON.get(
        outcome, "Your appeal is open and someone who was not involved will look at it."
    )
