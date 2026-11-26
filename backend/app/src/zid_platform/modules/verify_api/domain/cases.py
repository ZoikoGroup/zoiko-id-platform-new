"""Disputes, appeals and erasure requests for a partner's verifications (ZID-EXT-IDV-001 WF-14, WF-16, epic E18; CR-159). Pure rules, no I/O.

A company raises a CASE about one session or about one of its own customer references:
- DISPUTE: it disagrees with a recorded decision. - APPEAL: it asks for a person to look again at a not-verified or inconclusive result.
- ERASURE: it asks for its customer reference to be removed from the sessions it was sent with (a rights request from the company's own customer).
A person at Zoiko takes a case into review and a DIFFERENT person resolves or rejects it, with a reason the company can read. Nothing here changes a decision: a dispute or an appeal ends in a note, and if a new look
is wanted the company asks for a recheck, which makes a new decision version and keeps the old one. An erasure removes the customer reference and the free-form metadata from the sessions; the decisions stay (they hold
no personal data) so the audit trail and the usage record remain whole.

The company's customer reference is itself personal data, so a case holds only its hash: asking for erasure must not keep a second copy of what is to be erased.
"""

from __future__ import annotations

import hashlib
from enum import StrEnum

from zid_platform.modules.verify_api.domain.model import PartnerError

NOTE_MIN, NOTE_MAX = 3, 1000
REASON_MIN, REASON_MAX = 10, 1000


class CaseKind(StrEnum):
    DISPUTE = "DISPUTE"
    APPEAL = "APPEAL"
    ERASURE = "ERASURE"


class CaseStatus(StrEnum):
    OPEN = "OPEN"
    IN_REVIEW = "IN_REVIEW"
    RESOLVED = "RESOLVED"
    REJECTED = "REJECTED"
    WITHDRAWN = "WITHDRAWN"


OPEN_STATES = frozenset({CaseStatus.OPEN, CaseStatus.IN_REVIEW})
TRANSITIONS: dict[CaseStatus, frozenset[CaseStatus]] = {
    CaseStatus.OPEN: frozenset({CaseStatus.IN_REVIEW, CaseStatus.REJECTED, CaseStatus.WITHDRAWN}),
    CaseStatus.IN_REVIEW: frozenset(
        {CaseStatus.RESOLVED, CaseStatus.REJECTED, CaseStatus.WITHDRAWN}
    ),
    CaseStatus.RESOLVED: frozenset(),
    CaseStatus.REJECTED: frozenset(),
    CaseStatus.WITHDRAWN: frozenset(),
}
# a decision can be disputed or appealed only once there is one; an appeal only makes sense against a result that was not a pass
APPEALABLE_OUTCOMES = frozenset({"not_verified", "inconclusive", "review_required"})
DISPUTABLE_STATUSES = frozenset({"completed", "inconclusive", "manual_review", "action_required"})


def check_transition(current: CaseStatus, target: CaseStatus) -> CaseStatus:
    if target not in TRANSITIONS[current]:
        raise PartnerError(
            "CASE_STATE_INVALID",
            f"A {current.value.lower().replace('_', ' ')} case cannot become {target.value.lower().replace('_', ' ')}.",
        )
    return target


def check_kind(kind: str) -> CaseKind:
    try:
        return CaseKind(str(kind).upper())
    except ValueError as e:
        raise PartnerError("VALIDATION_FAILED", "Choose DISPUTE, APPEAL or ERASURE.") from e


def check_reason(reason: str | None) -> str:
    clean = " ".join((reason or "").split())
    if not REASON_MIN <= len(clean) <= REASON_MAX:
        raise PartnerError(
            "VALIDATION_FAILED",
            f"Say why in {REASON_MIN} to {REASON_MAX} characters, so the reviewer has something to act on.",
        )
    return clean


def check_note(note: str | None, *, required: bool) -> str:
    clean = " ".join((note or "").split())
    if len(clean) > NOTE_MAX:
        raise PartnerError("VALIDATION_FAILED", f"The note is longer than {NOTE_MAX} characters.")
    if required and len(clean) < NOTE_MIN:
        raise PartnerError("NOTE_REQUIRED", "Give a reason; the company reads it.")
    return clean


def check_reference(reference: str | None) -> str:
    clean = (reference or "").strip()
    if not 1 <= len(clean) <= 128:
        raise PartnerError("VALIDATION_FAILED", "Give the customer reference, 1 to 128 characters.")
    return clean


def reference_hash(tenant_id: str, reference: str) -> bytes:
    """A one-way fingerprint of a customer reference, scoped to the company so the same reference at two companies never matches. It cannot be reversed to the reference."""
    return hashlib.sha256(f"verify-api-case:{tenant_id}:{reference}".encode()).digest()


def check_target(kind: CaseKind, *, session_id: str | None, reference: str | None) -> None:
    """A dispute or an appeal is about one session; an erasure is about one customer reference. Never both, never neither."""
    if kind is CaseKind.ERASURE:
        if not reference or session_id:
            raise PartnerError(
                "VALIDATION_FAILED", "An erasure request names a customer reference, not a session."
            )
    elif not session_id or reference:
        raise PartnerError(
            "VALIDATION_FAILED",
            f"A {kind.value.lower()} names one session, not a customer reference.",
        )


def check_session_state(kind: CaseKind, session_status: str, outcome: str | None) -> None:
    """Only a session that has reached a result can be disputed; only a result that was not a pass can be appealed."""
    if session_status not in DISPUTABLE_STATUSES:
        raise PartnerError(
            "CASE_NOT_ALLOWED", "That session has no result yet, so there is nothing to dispute."
        )
    if kind is CaseKind.APPEAL and outcome not in APPEALABLE_OUTCOMES:
        raise PartnerError(
            "CASE_NOT_ALLOWED",
            "An appeal is for a result that was not verified. For a different concern, raise a dispute.",
        )
