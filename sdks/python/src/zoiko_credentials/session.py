"""The verification session state machine (ZID-INT-002 section 05.1): a pure mirror of `sdks/typescript/src/session.ts`.

The server is authoritative; this only stops a client doing or showing what the contract forbids:
- nothing is captured before consent;
- a corrupt payload never advances (it ends in RETRY_REQUIRED);
- OCR is not source authenticity: evidence goes to SOURCE_PENDING or REVIEW_REQUIRED, never straight to COMPLETE;
- an issuer outage keeps the session SOURCE_PENDING: there is no FAILED state;
- a human decision (REVIEW_REQUIRED to COMPLETE or REJECTED) needs a documented authority;
- withdrawing consent closes capture.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

SESSION_STATES = (
    "NEW",
    "CONSENT_REQUIRED",
    "READY",
    "COLLECTING",
    "RETRY_REQUIRED",
    "EVIDENCE_RECEIVED",
    "SOURCE_PENDING",
    "REVIEW_REQUIRED",
    "APPEALED",
    "COMPLETE",
    "REJECTED",
    "EXPIRED",
    "DECLINED",
    "CANCELED",
    "UNAVAILABLE",
)

_NEXT: dict[str, tuple[str, ...]] = {
    "NEW": ("CONSENT_REQUIRED", "UNAVAILABLE"),
    "CONSENT_REQUIRED": ("READY", "DECLINED"),
    "READY": ("COLLECTING", "CANCELED"),
    "COLLECTING": ("EVIDENCE_RECEIVED", "RETRY_REQUIRED", "CANCELED"),
    "RETRY_REQUIRED": ("COLLECTING", "CANCELED"),
    "EVIDENCE_RECEIVED": ("SOURCE_PENDING", "REVIEW_REQUIRED"),
    "SOURCE_PENDING": ("COMPLETE", "REVIEW_REQUIRED", "EXPIRED"),
    "REVIEW_REQUIRED": ("COMPLETE", "APPEALED", "REJECTED"),
    "APPEALED": ("REVIEW_REQUIRED",),
    "COMPLETE": (),
    "REJECTED": (),
    "EXPIRED": (),
    "DECLINED": (),
    "CANCELED": (),
    "UNAVAILABLE": (),
}
TERMINAL_STATES = tuple(s for s in SESSION_STATES if not _NEXT[s])

_EVENT_FOR = {
    "CONSENT_REQUIRED": "session.created",
    "READY": "consent.recorded",
    "COLLECTING": "capture.started",
    "EVIDENCE_RECEIVED": "document.submitted",
    "SOURCE_PENDING": "issuer.pending",
    "REVIEW_REQUIRED": "review.required",
    "COMPLETE": "session.completed",
    "CANCELED": "session.canceled",
}


class SessionError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class Transition:
    from_state: str
    to_state: str
    event: str | None


def can_move(from_state: str, to_state: str) -> bool:
    return to_state in _NEXT.get(from_state, ())


def is_terminal(state: str) -> bool:
    return not _NEXT[state]


def move(from_state: str, to_state: str, *, decided_by: str | None = None) -> Transition:
    """Move to `to_state`. A human decision at REVIEW_REQUIRED (to COMPLETE or REJECTED) must name its authority."""
    if not can_move(from_state, to_state):
        raise SessionError("TRANSITION_NOT_ALLOWED", f"{from_state} cannot move to {to_state}")
    if from_state == "REVIEW_REQUIRED" and to_state in ("COMPLETE", "REJECTED") and not (decided_by or "").strip():
        raise SessionError("AUTHORITY_REQUIRED", "a review decision needs a documented authority")
    return Transition(from_state, to_state, _EVENT_FOR.get(to_state))


def withdraw_consent(state: str) -> Transition:
    """Consent withdrawn: capture closes where it is open; a finished session is untouched."""
    if state in ("READY", "COLLECTING", "RETRY_REQUIRED"):
        return move(state, "CANCELED")
    if state == "CONSENT_REQUIRED":
        return move(state, "DECLINED")
    raise SessionError("TRANSITION_NOT_ALLOWED", f"consent cannot be withdrawn in {state}")


def after_capture(payload_intact: bool) -> str:
    return "EVIDENCE_RECEIVED" if payload_intact else "RETRY_REQUIRED"


def after_source(answer: Literal["CONFIRMED", "UNAVAILABLE", "UNCLEAR", "TIMED_OUT"]) -> str:
    """An outage is not a failure: the session waits."""
    return {
        "CONFIRMED": "COMPLETE",
        "UNCLEAR": "REVIEW_REQUIRED",
        "TIMED_OUT": "EXPIRED",
    }.get(answer, "SOURCE_PENDING")
