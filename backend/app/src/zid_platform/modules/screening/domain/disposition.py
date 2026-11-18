"""Decisions, holds, escalations and appeals on a screening case (ZID-DTV-WF-06 S05, S09, ENG-08; CR-113). Pure domain, no I/O.

A disposition is proposed by one person and finalised by another. What a person may propose depends on their ROLE, never on a screen: a customer administrator can request a
review and nothing else. A decision is appended, never edited: a correction or an appeal adds a new revision that cites the one before, and the earlier revision stays readable. A
decision made against a stale revision is refused (409), so a late save cannot overwrite a newer legal decision.

Who may do what:
  analyst       propose HOLD, EDD_REQUIRED, UNRESOLVED; propose a false positive or a restriction (needs a checker)
  senior        check and finalise what an analyst proposed (never their own)
  legal         the only role that may propose or finalise PROHIBITED and the downstream actions FREEZE, BLOCK and REPORT
  customer_admin  request_review only
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum

from zid_platform.modules.screening.domain.coverage import Disposition


class DispositionError(ValueError):
    def __init__(self, code: str, detail: str = "", http_status: int = 422) -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail
        self.http_status = http_status


class Role(StrEnum):
    ANALYST = "analyst"
    SENIOR = "senior"
    LEGAL = "legal"
    CUSTOMER_ADMIN = "customer_admin"


class Action(StrEnum):
    RELEASE = "RELEASE"
    EDD = "EDD"
    HOLD = "HOLD"
    REJECT = "REJECT"
    FREEZE = "FREEZE"
    BLOCK = "BLOCK"
    REPORT = "REPORT"


LEGAL_ONLY_ACTIONS = frozenset({Action.FREEZE, Action.BLOCK, Action.REPORT})
LEGAL_ONLY_DISPOSITIONS = frozenset({Disposition.PROHIBITED})
# a decision that lets a held party through, or stops one, is made by two people
DUAL_CONTROL = frozenset(
    {Disposition.CLEARED_FOR_PURPOSE, Disposition.RESTRICTED, Disposition.PROHIBITED}
)
_ALLOWED_ACTION = {
    Disposition.CLEARED_FOR_PURPOSE: frozenset({Action.RELEASE}),
    Disposition.EDD_REQUIRED: frozenset({Action.EDD, Action.HOLD}),
    Disposition.HOLD_PENDING_REVIEW: frozenset({Action.HOLD}),
    Disposition.RESTRICTED: frozenset({Action.HOLD, Action.REJECT}),
    Disposition.PROHIBITED: frozenset({Action.REJECT, Action.FREEZE, Action.BLOCK, Action.REPORT}),
    Disposition.UNRESOLVED: frozenset({Action.HOLD}),
}
EVIDENCE_REQUIRED = frozenset(
    {Disposition.CLEARED_FOR_PURPOSE, Disposition.RESTRICTED, Disposition.PROHIBITED}
)


@dataclass(frozen=True, slots=True)
class Proposal:
    case_id: str
    expected_revision: int
    disposition: Disposition
    action: Action
    maker: str
    maker_role: Role
    rationale: str
    reason_codes: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    policy_version: str
    legal_citation: str | None = None
    review_by: datetime | None = None  # when the decision must be looked at again
    proposed_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class Revision:
    """One entry of a case's append-only history."""

    case_id: str
    revision: int
    disposition: Disposition
    action: Action
    maker: str
    checker: str | None
    rationale: str
    reason_codes: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    policy_version: str
    legal_citation: str | None
    review_by: datetime | None
    decided_at: datetime
    supersedes: int | None = None
    kind: str = "DECISION"  # DECISION | APPEAL | CORRECTION | REOPEN
    case_hash: str = ""


def _may_propose(role: Role, proposal: Proposal) -> None:
    if role is Role.CUSTOMER_ADMIN:
        raise DispositionError(
            "ROLE_CANNOT_DECIDE", "a customer administrator may request a review, nothing more", 403
        )
    if proposal.disposition in LEGAL_ONLY_DISPOSITIONS and role is not Role.LEGAL:
        raise DispositionError("LEGAL_ROLE_REQUIRED", proposal.disposition.value, 403)
    if proposal.action in LEGAL_ONLY_ACTIONS and role is not Role.LEGAL:
        raise DispositionError("LEGAL_ROLE_REQUIRED", proposal.action.value, 403)


def check_proposal(
    proposal: Proposal, *, current_revision: int, subject_refs: Sequence[str] = ()
) -> None:
    """Everything that can be refused before a second person is asked."""
    _may_propose(proposal.maker_role, proposal)
    if proposal.expected_revision != current_revision:
        raise DispositionError(
            "REVISION_CONFLICT", f"the case is at revision {current_revision}", http_status=409
        )
    if proposal.maker in subject_refs:
        raise DispositionError("MAKER_IS_A_SUBJECT", http_status=403)
    if proposal.action not in _ALLOWED_ACTION[proposal.disposition]:
        raise DispositionError(
            "ACTION_NOT_FOR_DISPOSITION",
            f"{proposal.action.value} for {proposal.disposition.value}",
        )
    if len(proposal.rationale.strip()) < 20:
        raise DispositionError("RATIONALE_REQUIRED", "a decision needs a recorded reason")
    if not proposal.reason_codes:
        raise DispositionError("REASON_CODES_REQUIRED")
    if proposal.disposition in EVIDENCE_REQUIRED and not proposal.evidence_refs:
        raise DispositionError("EVIDENCE_REQUIRED", "cite the evidence that supports the decision")
    if (
        proposal.disposition in (Disposition.RESTRICTED, Disposition.PROHIBITED)
        and not proposal.policy_version
    ):
        raise DispositionError("POLICY_REQUIRED")
    if (
        proposal.disposition is Disposition.PROHIBITED or proposal.action in LEGAL_ONLY_ACTIONS
    ) and not (proposal.legal_citation or "").strip():
        raise DispositionError("LEGAL_CITATION_REQUIRED")
    if (
        proposal.disposition
        in (Disposition.RESTRICTED, Disposition.PROHIBITED, Disposition.UNRESOLVED)
        and proposal.review_by is None
    ):
        raise DispositionError("REVIEW_DATE_REQUIRED", "say when the decision is looked at again")


def check_checker(
    proposal: Proposal, *, checker: str, checker_role: Role, earlier_deciders: Sequence[str]
) -> None:
    """The second person. Not the maker, not anyone who decided this case before, and with the authority the decision needs."""
    if checker == proposal.maker:
        raise DispositionError("CHECKER_IS_THE_MAKER", http_status=403)
    if checker in earlier_deciders:
        raise DispositionError("CHECKER_DECIDED_BEFORE", http_status=403)
    if checker_role is Role.CUSTOMER_ADMIN:
        raise DispositionError("ROLE_CANNOT_DECIDE", http_status=403)
    if (
        proposal.disposition in LEGAL_ONLY_DISPOSITIONS or proposal.action in LEGAL_ONLY_ACTIONS
    ) and checker_role is not Role.LEGAL:
        raise DispositionError("LEGAL_ROLE_REQUIRED", "a legal decision needs a legal checker", 403)
    if checker_role is Role.ANALYST and proposal.disposition in DUAL_CONTROL:
        raise DispositionError("CHECKER_NEEDS_SENIOR_ROLE", http_status=403)


def needs_checker(proposal: Proposal) -> bool:
    return proposal.disposition in DUAL_CONTROL or proposal.action in LEGAL_ONLY_ACTIONS


def _hash(r: Revision) -> str:
    material = "|".join(
        (
            r.case_id,
            str(r.revision),
            r.disposition.value,
            r.action.value,
            r.maker,
            r.checker or "",
            r.rationale,
            ",".join(r.reason_codes),
            ",".join(r.evidence_refs),
            r.policy_version,
            r.legal_citation or "",
            r.decided_at.isoformat(),
            str(r.supersedes or 0),
            r.kind,
        )
    )
    return hashlib.sha256(material.encode()).hexdigest()


def append(
    history: Sequence[Revision],
    proposal: Proposal,
    *,
    checker: str | None,
    checker_role: Role | None,
    at: datetime,
    kind: str = "DECISION",
    subject_refs: Sequence[str] = (),
) -> list[Revision]:
    """Add one revision. The earlier ones are returned untouched. A decision that needs a checker without one is refused, so it can never be half-made."""
    current = history[-1].revision if history else 0
    check_proposal(proposal, current_revision=current, subject_refs=subject_refs)
    if needs_checker(proposal):
        if checker is None or checker_role is None:
            raise DispositionError(
                "CHECKER_REQUIRED", "a second person must finalise this decision"
            )
        check_checker(
            proposal,
            checker=checker,
            checker_role=checker_role,
            earlier_deciders=[h.maker for h in history if h.kind == "DECISION"]
            if kind == "APPEAL"
            else [],
        )
    revision = Revision(
        case_id=proposal.case_id,
        revision=current + 1,
        disposition=proposal.disposition,
        action=proposal.action,
        maker=proposal.maker,
        checker=checker,
        rationale=proposal.rationale.strip(),
        reason_codes=proposal.reason_codes,
        evidence_refs=proposal.evidence_refs,
        policy_version=proposal.policy_version,
        legal_citation=proposal.legal_citation,
        review_by=proposal.review_by,
        decided_at=at,
        supersedes=current or None,
        kind=kind,
    )
    return [*history, replace(revision, case_hash=_hash(revision))]


def current(history: Sequence[Revision]) -> Revision | None:
    return history[-1] if history else None


def verify_chain(history: Sequence[Revision]) -> bool:
    """Whether every revision is intact and cites the one before: an auditor's check that nothing was edited or removed."""
    for i, r in enumerate(history):
        if r.revision != i + 1 or r.supersedes != (i or None):
            return False
        if r.case_hash != _hash(replace(r, case_hash="")):
            return False
    return True


def open_appeal(
    history: Sequence[Revision],
    *,
    case_id: str,
    appellant_note: str,
    reviewer: str,
    reviewer_role: Role,
    new_disposition: Disposition,
    action: Action,
    rationale: str,
    reason_codes: tuple[str, ...],
    evidence_refs: tuple[str, ...],
    policy_version: str,
    checker: str | None,
    checker_role: Role | None,
    at: datetime,
    legal_citation: str | None = None,
    review_by: datetime | None = None,
) -> list[Revision]:
    """An appeal or a correction is a new revision by someone who did not decide the case before. The original decision stays in the history and in the evidence."""
    if len(appellant_note.strip()) < 20:
        raise DispositionError("STATEMENT_INVALID", "write at least a sentence")
    if any(h.maker == reviewer or h.checker == reviewer for h in history):
        raise DispositionError(
            "REVIEWER_CONFLICT",
            "an appeal is decided by someone who did not decide the case before",
            403,
        )
    proposal = Proposal(
        case_id,
        history[-1].revision if history else 0,
        new_disposition,
        action,
        reviewer,
        reviewer_role,
        rationale,
        reason_codes,
        evidence_refs,
        policy_version,
        legal_citation,
        review_by,
    )
    return append(
        history, proposal, checker=checker, checker_role=checker_role, at=at, kind="APPEAL"
    )


SYSTEM = "system:source-release"


REOPEN_RATIONALE = {
    "ENTRY_DELISTED_OR_CHANGED": "A list entry this case relied on was delisted or changed in a new release, so the case is held for a person to look again.",
    "ARTICLE_RETRACTED": "The provider retracted an article this case relied on, so the case is held for a person to look again. A retraction does not by itself clear the subject.",
    "ARTICLE_CORRECTED": "The provider corrected an article this case relied on, so the case is held for a person to look again. A correction does not by itself raise or lower the risk.",
}


def reopen(
    history: Sequence[Revision],
    *,
    case_id: str,
    reason_codes: tuple[str, ...],
    evidence_ref: str,
    policy_version: str,
    at: datetime,
) -> list[Revision]:
    """A case a new list release has put in doubt goes back to a hold, as a new revision by the system that cites the one before. Nobody decided this and nobody is named as deciding it; a person
    decides what the hold becomes. The earlier decisions stay."""
    current = history[-1].revision if history else 0
    revision = Revision(
        case_id=case_id,
        revision=current + 1,
        disposition=Disposition.HOLD_PENDING_REVIEW,
        action=Action.HOLD,
        maker=SYSTEM,
        checker=None,
        rationale=REOPEN_RATIONALE.get(
            reason_codes[0] if reason_codes else "", REOPEN_RATIONALE["ENTRY_DELISTED_OR_CHANGED"]
        ),
        reason_codes=reason_codes,
        evidence_refs=(evidence_ref,),
        policy_version=policy_version,
        legal_citation=None,
        review_by=None,
        decided_at=at,
        supersedes=current or None,
        kind="REOPEN",
    )
    return [*history, replace(revision, case_hash=_hash(revision))]


def event_id(case_id: str, revision: int, action: Action) -> str:
    """The id of the downstream event for one decision. The same decision always gives the same id, so a retry cannot suspend or release twice."""
    return hashlib.sha256(
        f"screening.disposition|{case_id}|{revision}|{action.value}".encode()
    ).hexdigest()[:32]


@dataclass(frozen=True, slots=True)
class CustomerRequest:
    """What a customer administrator can do: ask for a look. It decides nothing."""

    case_id: str
    requested_by: str
    note: str


def request_review(case_id: str, *, requested_by: str, role: Role, note: str) -> CustomerRequest:
    if len(note.strip()) < 10:
        raise DispositionError("STATEMENT_INVALID", "say what needs another look")
    return CustomerRequest(case_id, requested_by, note.strip())
