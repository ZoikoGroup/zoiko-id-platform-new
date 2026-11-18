"""Asking a customer for more information during enhanced due diligence (ZID-DTV-WF-06 section 10, EDD; CR-118). Pure state machine, no I/O.

What a request is: a reviewer asks for named items from a fixed catalogue, with a due date. The customer is told only that more information is needed to finish their check and which items; the text never says
or implies that a sanctions, PEP or media check is behind it (that is not for the customer to be told). The rules:

- a request names at least one catalogue item and is due in 3 to 60 days;
- it is RECEIVED only when every named item has been provided; a partial answer stays REQUESTED and says what is still missing;
- someone other than the person who asked reviews what came back, and a review accepts it or marks it INSUFFICIENT with a code (and the request may be raised again);
- it EXPIRES at the due date. Expiry clears nothing: the case stays held and a person decides what the silence means;
- reminders go out at half the window and at nine tenths of it, at most `MAX_REMINDERS`, never after it is answered or expired.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from enum import StrEnum

CATALOGUE = frozenset(
    {
        "SOURCE_OF_FUNDS",
        "SOURCE_OF_WEALTH",
        "OWNERSHIP_EXPLANATION",
        "IDENTITY_CLARIFICATION",
        "PURPOSE_OF_RELATIONSHIP",
        "SUPPORTING_DOCUMENT",
    }
)
MIN_DAYS, MAX_DAYS, MAX_REMINDERS = 3, 60, 2
INSUFFICIENT_CODES = frozenset(
    {"INCOMPLETE", "UNREADABLE", "INCONSISTENT", "NOT_SUPPORTED_BY_DOCUMENTS"}
)


class EddError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


class State(StrEnum):
    REQUESTED = "REQUESTED"
    RECEIVED = "RECEIVED"
    ACCEPTED = "ACCEPTED"
    INSUFFICIENT = "INSUFFICIENT"
    EXPIRED = "EXPIRED"


@dataclass(frozen=True, slots=True)
class Request:
    request_id: str
    case_id: str
    items: tuple[str, ...]
    requested_by: str
    opened_at: datetime
    due_at: datetime
    state: State = State.REQUESTED
    provided: frozenset[str] = frozenset()
    reminders: tuple[datetime, ...] = field(default=())
    reviewed_by: str | None = None
    review_code: str | None = None

    @property
    def missing(self) -> tuple[str, ...]:
        return tuple(i for i in self.items if i not in self.provided)


def open_request(
    request_id: str,
    case_id: str,
    items: Iterable[str],
    *,
    requested_by: str,
    now: datetime,
    due_in_days: int,
) -> Request:
    chosen = tuple(dict.fromkeys(items))
    if not chosen or any(i not in CATALOGUE for i in chosen):
        raise EddError("ITEMS_INVALID", "name one or more items from the catalogue")
    if not MIN_DAYS <= due_in_days <= MAX_DAYS:
        raise EddError("DUE_DATE_INVALID", f"{MIN_DAYS} to {MAX_DAYS} days")
    return Request(
        request_id, case_id, chosen, requested_by, now, now + timedelta(days=due_in_days)
    )


def _open(r: Request) -> None:
    if r.state in (State.ACCEPTED, State.EXPIRED):
        raise EddError("REQUEST_CLOSED", r.state.value)


def receive(r: Request, provided: Iterable[str], *, now: datetime) -> Request:
    """The customer's answer. Items that were not asked for are ignored; the request is RECEIVED only when nothing is missing."""
    _open(r)
    if now > r.due_at:
        raise EddError("REQUEST_EXPIRED", "past the due date")
    have = r.provided | (frozenset(provided) & frozenset(r.items))
    done = all(i in have for i in r.items)
    return replace(r, provided=have, state=State.RECEIVED if done else State.REQUESTED)


def review(r: Request, *, reviewer: str, accepted: bool, code: str | None = None) -> Request:
    if r.state is not State.RECEIVED:
        raise EddError("NOTHING_TO_REVIEW", r.state.value)
    if reviewer == r.requested_by:
        raise EddError(
            "REVIEWER_ASKED_FOR_IT", "someone other than the person who asked reviews the answer"
        )
    if accepted:
        return replace(r, state=State.ACCEPTED, reviewed_by=reviewer, review_code=None)
    if code not in INSUFFICIENT_CODES:
        raise EddError("REVIEW_CODE_REQUIRED", "say why it is not enough, with a code")
    return replace(r, state=State.INSUFFICIENT, reviewed_by=reviewer, review_code=code)


def tick(r: Request, *, now: datetime) -> tuple[Request, tuple[str, ...]]:
    """Time passing. Returns the request and the events: `REMINDER` or `EXPIRED`. Expiry clears nothing."""
    if r.state in (State.ACCEPTED, State.EXPIRED, State.INSUFFICIENT, State.RECEIVED):
        return r, ()
    if now > r.due_at:
        return replace(r, state=State.EXPIRED), ("EXPIRED",)
    window = (r.due_at - r.opened_at).total_seconds()
    used = (now - r.opened_at).total_seconds() / window if window else 1.0
    due_marks = sum(1 for p in (0.5, 0.9) if used >= p)
    if len(r.reminders) < min(due_marks, MAX_REMINDERS):
        return replace(r, reminders=(*r.reminders, now)), ("REMINDER",)
    return r, ()


def customer_text(r: Request) -> str:
    """The wording a customer sees. It never mentions screening, sanctions, politically exposed persons or media."""
    labels = {
        "SOURCE_OF_FUNDS": "where the money for this relationship comes from",
        "SOURCE_OF_WEALTH": "how your overall wealth was built",
        "OWNERSHIP_EXPLANATION": "an explanation of who owns or controls the company",
        "IDENTITY_CLARIFICATION": "a clarification of your identity details",
        "PURPOSE_OF_RELATIONSHIP": "what you intend to use the account for",
        "SUPPORTING_DOCUMENT": "a supporting document",
    }
    wanted = ", ".join(labels[i] for i in r.missing) or "nothing further"
    return f"To finish your check we need a little more information: {wanted}. Please reply by {r.due_at.date().isoformat()}."
