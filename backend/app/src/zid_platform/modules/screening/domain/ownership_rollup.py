"""What a company's screening says once its owners have been screened too (ZID-DTV-WF-06 section 12, QA-029 to QA-033; CR-120). Pure: owners and their screens in, one roll-up out.

A company is only as screened as the people behind it. The ownership analysis (WF02) names the candidates a person must confirm as owners or controllers; each is screened as a person (a UBO case). This module
combines those results, and it never lets absence read as clear:

- no owner identified at all is `INCOMPLETE` (an unknown structure is not a clean one);
- an owner with no screening case, or whose own identity is not yet evidenced, is `INCOMPLETE`;
- a screen older than the allowed age, or one that closed unresolved, is `INCOMPLETE`;
- any owner still waiting for a person is `IN_REVIEW`;
- any owner whose case closed restricted makes the company `RESTRICTED`, and the owner is `designated` for the ownership rules (a candidate that is merely a possible match is never a designation);
- only when every owner has a current screen that closed clear for its scope is the company `COMPLETE_CLEAR`.
Priority when several apply: RESTRICTED, then IN_REVIEW, then INCOMPLETE, then COMPLETE_CLEAR.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum


class Rollup(StrEnum):
    COMPLETE_CLEAR = "COMPLETE_CLEAR"
    IN_REVIEW = "IN_REVIEW"
    INCOMPLETE = "INCOMPLETE"
    RESTRICTED = "RESTRICTED"


@dataclass(frozen=True, slots=True)
class Owner:
    person_ref: str
    status: str  # the ownership analysis's word: NEEDS_EVIDENCE | NEEDS_CONFIRMATION | CONFIRMED


@dataclass(frozen=True, slots=True)
class OwnerScreen:
    person_ref: str
    case_state: str  # REVIEW_REQUIRED | SOURCE_UNAVAILABLE | NO_CANDIDATE | CLOSED_CLEAR_FOR_SCOPE | CLOSED_RESTRICTED | CLOSED_UNRESOLVED
    screened_at: datetime


@dataclass(frozen=True, slots=True)
class OwnerVerdict:
    person_ref: str
    verdict: Rollup
    reason: str


@dataclass(frozen=True, slots=True)
class Result:
    status: Rollup
    owners: tuple[OwnerVerdict, ...]
    reasons: tuple[str, ...]
    designated: frozenset[
        str
    ]  # owners whose own case closed restricted: the input the ownership rules take


_PRIORITY = {
    Rollup.RESTRICTED: 3,
    Rollup.IN_REVIEW: 2,
    Rollup.INCOMPLETE: 1,
    Rollup.COMPLETE_CLEAR: 0,
}
_CLEAR = {"CLOSED_CLEAR_FOR_SCOPE", "NO_CANDIDATE"}
_WAITING = {"REVIEW_REQUIRED", "SOURCE_UNAVAILABLE"}


def _one(
    owner: Owner, screen: OwnerScreen | None, *, now: datetime, max_age: timedelta
) -> OwnerVerdict:
    if owner.status == "NEEDS_EVIDENCE":
        return OwnerVerdict(
            owner.person_ref, Rollup.INCOMPLETE, "OWNER_NOT_EVIDENCED"
        )  # their identity is not yet established, so a screen of them would prove little
    if screen is None:
        return OwnerVerdict(owner.person_ref, Rollup.INCOMPLETE, "OWNER_NOT_SCREENED")
    if screen.case_state == "CLOSED_RESTRICTED":
        return OwnerVerdict(owner.person_ref, Rollup.RESTRICTED, "OWNER_CASE_RESTRICTED")
    if screen.case_state in _WAITING:
        return OwnerVerdict(owner.person_ref, Rollup.IN_REVIEW, "OWNER_CASE_IN_REVIEW")
    if now - screen.screened_at > max_age:
        return OwnerVerdict(owner.person_ref, Rollup.INCOMPLETE, "OWNER_CHECK_STALE")
    if screen.case_state in _CLEAR:
        return OwnerVerdict(owner.person_ref, Rollup.COMPLETE_CLEAR, "OWNER_CLEAR_FOR_SCOPE")
    return OwnerVerdict(owner.person_ref, Rollup.INCOMPLETE, "OWNER_CASE_UNRESOLVED")


def rollup(
    owners: Iterable[Owner], screens: Iterable[OwnerScreen], *, now: datetime, max_age: timedelta
) -> Result:
    owner_list = list(dict.fromkeys(owners))
    by_person: dict[str, OwnerScreen] = {}
    for s in screens:
        held = by_person.get(s.person_ref)
        if held is None or s.screened_at > held.screened_at:
            by_person[s.person_ref] = s  # the latest screen of a person is the one that counts
    if not owner_list:
        return Result(Rollup.INCOMPLETE, (), ("NO_OWNERS_IDENTIFIED",), frozenset())
    verdicts = tuple(
        _one(o, by_person.get(o.person_ref), now=now, max_age=max_age) for o in owner_list
    )
    status = max((v.verdict for v in verdicts), key=lambda r: _PRIORITY[r])
    reasons = tuple(sorted({v.reason for v in verdicts if v.verdict is not Rollup.COMPLETE_CLEAR}))
    designated = frozenset(v.person_ref for v in verdicts if v.verdict is Rollup.RESTRICTED)
    return Result(status, verdicts, reasons, designated)


def needs_screening(
    owners: Iterable[Owner], screens: Iterable[OwnerScreen], *, now: datetime, max_age: timedelta
) -> tuple[str, ...]:
    """The owners a new case should be opened for: those with no screen or a stale one. An owner whose identity is not yet evidenced is not listed (a case for them would be refused for want of an identity claim), and one already waiting for a person or closed in time is left alone."""
    result = rollup(owners, screens, now=now, max_age=max_age)
    wanted = {
        v.person_ref
        for v in result.owners
        if v.reason in ("OWNER_NOT_SCREENED", "OWNER_CHECK_STALE")
    }
    return tuple(o.person_ref for o in dict.fromkeys(owners) if o.person_ref in wanted)


def customer_view(result: Result) -> dict[str, object]:
    """What the company itself may be told. A restricted owner reads exactly like one still in review: saying otherwise would tell the company what a check found. No designation list leaves here."""

    def soften(v: Rollup) -> str:
        return Rollup.IN_REVIEW.value if v is Rollup.RESTRICTED else v.value

    def soften_reason(code: str) -> str:
        return "OWNER_CASE_IN_REVIEW" if code == "OWNER_CASE_RESTRICTED" else code

    return {
        "status": soften(result.status),
        "owners": [
            {
                "person_ref": v.person_ref,
                "status": soften(v.verdict),
                "reason": soften_reason(v.reason),
            }
            for v in result.owners
        ],
        "reasons": sorted({soften_reason(r) for r in result.reasons}),
    }
