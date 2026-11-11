"""The verification case: pure rules (ZID-INT-002 sections 04, 05.1 and 14).

A case groups the separate checks one person is asked to complete. The rules that matter:
- every requested check is its OWN task with its OWN outcome; a case is never "passed", it has a state and a list of outcomes;
- a check name the platform does not know is refused before anything is created (nothing is asked of any source);
- a finished case (COMPLETE, CANCELED, EXPIRED) is never mutated: a new case is needed (409 incompatible_state);
- a case whose time has run out with checks still open is EXPIRED, not failed;
- an identity task is complete only when the linked identity verification session is itself in a final state.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from zid_platform.modules.credential.domain.claims import ClaimType

# `requested_checks` names (section 04) -> the claim class a credential check runs; identity is a different kind of task
CHECK_KINDS: Mapping[str, ClaimType | None] = {
    "identity": None,
    "degree": ClaimType.ACADEMIC_AWARD,
    "license": ClaimType.PROFESSIONAL_LICENSE,
    "insurance": ClaimType.INSURANCE_ASSERTION,
    "employment": ClaimType.EMPLOYMENT_ASSERTION,
    "cpd": ClaimType.CPD_RECORD,
    "certification": ClaimType.INDUSTRY_CERTIFICATE,
}
# the section 08 service code of each credential check, used to ask for its country pack
SERVICE_OF: Mapping[str, str] = {
    "degree": "academic.verify",
    "license": "license.verify",
    "insurance": "insurance.verify",
    "employment": "employment.verify",
    "cpd": "cpd.verify",
    "certification": "credential.verify",
}


class CaseError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


class CaseState(StrEnum):
    PENDING = "PENDING"
    COMPLETE = "COMPLETE"
    CANCELED = "CANCELED"
    EXPIRED = "EXPIRED"


class TaskState(StrEnum):
    PENDING_INPUT = "PENDING_INPUT"
    IN_PROGRESS = "IN_PROGRESS"  # an identity session is linked but not final
    COMPLETE = "COMPLETE"


FINAL = frozenset({CaseState.COMPLETE, CaseState.CANCELED, CaseState.EXPIRED})


def validate_checks(requested: Iterable[str]) -> list[str]:
    """The requested checks, once each and in the order given. An empty or unknown request creates nothing."""
    out: list[str] = []
    for name in requested:
        if name not in CHECK_KINDS:
            raise CaseError("CHECK_NOT_SUPPORTED", name)
        if name not in out:
            out.append(name)
    if not out:
        raise CaseError("CHECK_NOT_SUPPORTED", "no check requested")
    return out


def case_state(
    current: CaseState, tasks: Sequence[TaskState], *, now: datetime, expires_at: datetime
) -> CaseState:
    """The state of a case from its tasks. A final state is sticky; a pending case with every task complete is COMPLETE; a pending
    case past its time with work still open is EXPIRED (never failed)."""
    if current in FINAL:
        return current
    if tasks and all(t is TaskState.COMPLETE for t in tasks):
        return CaseState.COMPLETE
    if now >= expires_at:
        return CaseState.EXPIRED
    return CaseState.PENDING


def require_open(state: CaseState) -> None:
    """A finished case preserves its record: changing it needs a new case."""
    if state in FINAL:
        raise CaseError("INCOMPATIBLE_STATE", state.value)


def identity_outcome(session_status: str) -> tuple[str | None, TaskState]:
    """Map an identity verification session's status to a section 06.1 outcome name. A session that is not final has no outcome yet.
    The provider's FAILED is a verdict on the evidence (NOT_VERIFIED); a session that needs a person is INDETERMINATE."""
    s = session_status.upper()
    if s == "VERIFIED":
        return "VERIFIED", TaskState.COMPLETE
    if s == "FAILED":
        return "NOT_VERIFIED", TaskState.COMPLETE
    if s == "EXPIRED":
        return "EXPIRED", TaskState.COMPLETE
    if s == "CANCELED":
        return "INDETERMINATE", TaskState.COMPLETE  # nothing was decided about the person
    return (
        None,
        TaskState.IN_PROGRESS,
    )  # CREATED, IN_PROGRESS, PENDING_EXTERNAL, MANUAL_REVIEW, RESUBMISSION_REQUIRED


@dataclass(frozen=True, slots=True)
class TaskView:
    check: str
    state: TaskState
    outcome: str | None
    credential_ref: str | None = None
    identity_verification_ref: str | None = None


def pending_work(tasks: Sequence[TaskView]) -> list[dict[str, str]]:
    """The typed tasks still waiting, and what each needs (the applicant sees the next action)."""
    out = []
    for t in tasks:
        if t.state is TaskState.COMPLETE:
            continue
        needs = (
            "link an identity verification (verification_id)"
            if t.check == "identity"
            else "submit issuer_claim and credential_claim"
        )
        out.append({"check": t.check, "state": t.state.value, "needs": needs})
    return out
