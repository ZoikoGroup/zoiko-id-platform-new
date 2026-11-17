"""Two independent reviewers for a company approval that no authoritative source backs (ZID-DTV-WF-02 K07; acceptance T10, T11). Pure domain, no I/O.

When a company case has no authoritative record behind it (no register or proven supplier returned the company), the only evidence is what the applicant supplied, so one
person's approval is not enough:

- the first reviewer's approval is recorded as a first finding and the case stays in review (`REVIEWER_1_ACCEPTED`);
- a second reviewer who is not the first, and not the person who requested the check, either agrees (the case is VERIFIED, `DUAL_REVIEW_AGREED`) or disagrees. A material
  disagreement is never settled by whoever writes last: it escalates to Compliance (`ESCALATED_TO_COMPLIANCE`);
- an escalated case is decided only by someone authorised for escalations, who is none of the earlier reviewers;
- a rejection is fail-safe and needs only one reviewer; a request for more evidence needs one;
- a case an authoritative source backs keeps the single-reviewer path it always had.

Nothing here changes a past row: each step is a new decision that answers the previous one.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from zid_platform.modules.organization.domain.kyb import KybError, KybStatus

FIRST_ACCEPTED = "REVIEWER_1_ACCEPTED"
AGREED = "DUAL_REVIEW_AGREED"
ESCALATED = "ESCALATED_TO_COMPLIANCE"
DISAGREEMENT = "REVIEWER_DISAGREEMENT"
COMPLIANCE = "COMPLIANCE_DECISION"
EVIDENCE_REQUESTED = "REVIEWER_REQUESTED_EVIDENCE"
# reason codes that describe the position of the review and must not be carried from one ruling into the next
POSITION_MARKERS = frozenset(
    {FIRST_ACCEPTED, AGREED, ESCALATED, DISAGREEMENT, COMPLIANCE, EVIDENCE_REQUESTED}
)


@dataclass(frozen=True, slots=True)
class Row:
    """What the rule needs from one past decision: who made it (None for a system decision), its status and its reason codes."""

    decided_by: str | None
    status: KybStatus
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Step:
    outcome: KybStatus  # the status the new decision row carries
    reasons: tuple[str, ...]  # position markers for the new row
    final: bool  # True when the case is decided


def requires_dual(evidence_body: Mapping[str, object]) -> bool:
    """True when no authoritative record backs the case: the decision's own evidence names no source authority and no registration status."""
    return (
        evidence_body.get("source_authority") is None
        and evidence_body.get("registration_status") is None
    )


def _round(history: Sequence[Row]) -> list[Row]:
    """The reviewer rulings since the last system decision: the people who have already looked at this case in this round."""
    out: list[Row] = []
    for row in reversed(history):
        if row.decided_by is None:
            break
        out.append(row)
    return out


def reviewers_so_far(history: Sequence[Row]) -> frozenset[str]:
    return frozenset(r.decided_by for r in _round(history) if r.decided_by)


def is_escalated(history: Sequence[Row]) -> bool:
    return (
        bool(history)
        and ESCALATED in history[-1].reasons
        and history[-1].status is KybStatus.NEEDS_REVIEW
    )


def next_step(
    history: Sequence[Row],
    *,
    reviewer: str,
    outcome: KybStatus,
    dual_required: bool,
    escalation_authorized: bool = False,
) -> Step:
    """What the reviewer's ruling does. Raises KybError for a reviewer who is not independent or not authorised for an escalated case."""
    if not dual_required:
        return Step(outcome, (), final=outcome is not KybStatus.NEEDS_REVIEW)
    seen = reviewers_so_far(history)
    if reviewer in seen:
        raise KybError(
            "REVIEWER_NOT_INDEPENDENT", "a different reviewer must give the second opinion"
        )
    last = history[-1] if history else None
    if last is not None and is_escalated(history):
        if not escalation_authorized:
            raise KybError(
                "ESCALATION_NEEDS_COMPLIANCE",
                "an escalated case is decided by someone authorised for escalations",
            )
        if outcome is KybStatus.NEEDS_REVIEW:
            return Step(KybStatus.NEEDS_REVIEW, (ESCALATED, EVIDENCE_REQUESTED), final=False)
        return Step(outcome, (COMPLIANCE,), final=True)
    awaiting = (
        last is not None
        and FIRST_ACCEPTED in last.reasons
        and last.status is KybStatus.NEEDS_REVIEW
    )
    if awaiting:
        if outcome is KybStatus.VERIFIED:
            return Step(KybStatus.VERIFIED, (AGREED,), final=True)
        if outcome is KybStatus.REJECTED:
            return Step(
                KybStatus.NEEDS_REVIEW, (ESCALATED, DISAGREEMENT), final=False
            )  # never last-write-wins
        return Step(KybStatus.NEEDS_REVIEW, (EVIDENCE_REQUESTED,), final=False)
    if outcome is KybStatus.VERIFIED:
        return Step(KybStatus.NEEDS_REVIEW, (FIRST_ACCEPTED,), final=False)
    if outcome is KybStatus.REJECTED:
        return Step(
            KybStatus.REJECTED, (), final=True
        )  # a rejection protects: one reviewer is enough
    return Step(KybStatus.NEEDS_REVIEW, (EVIDENCE_REQUESTED,), final=False)


def position(status: KybStatus, reasons: Sequence[str]) -> str:
    """Where the case stands for the people reviewing it: a stable code for a screen to turn into words."""
    if status is KybStatus.NEEDS_REVIEW and ESCALATED in reasons:
        return "ESCALATED_TO_COMPLIANCE"
    if status is KybStatus.NEEDS_REVIEW and FIRST_ACCEPTED in reasons:
        return "AWAITING_SECOND_REVIEWER"
    if status is KybStatus.VERIFIED and AGREED in reasons:
        return "APPROVED_BY_TWO_REVIEWERS"
    if status is KybStatus.VERIFIED and COMPLIANCE in reasons:
        return "DECIDED_BY_COMPLIANCE"
    if status is KybStatus.REJECTED and COMPLIANCE in reasons:
        return "DECIDED_BY_COMPLIANCE"
    return "DECIDED" if status in (KybStatus.VERIFIED, KybStatus.REJECTED) else "IN_REVIEW"
