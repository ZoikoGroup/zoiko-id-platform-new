"""Plan requests: what a company may ask for and how the answer is recorded (ZID-COM-001 §10; CR-032). Pure domain.

A request never changes an entitlement. It is the company's recorded wish to move to a published plan; the move itself
is a signed order raised and approved by the people with the authority to do that. The rules here are only about
whether the ask makes sense and which answers are allowed.
"""

from __future__ import annotations

from collections.abc import Collection

MAX_NOTE = 1000
OPEN, ACCEPTED, DECLINED, WITHDRAWN = "OPEN", "ACCEPTED", "DECLINED", "WITHDRAWN"
ANSWERS = frozenset({ACCEPTED, DECLINED})


class PlanRequestError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


def check_request(
    *, plan: str, current: str, published: Collection[str], note: str | None, has_open: bool
) -> str:
    """The cleaned note for a request that may be opened, or the reason it may not."""
    if plan not in published:
        raise PlanRequestError("PLAN_NOT_AVAILABLE", f"{plan} is not a published plan")
    if plan == current:
        raise PlanRequestError("PLAN_ALREADY_CURRENT", "the company is already on this plan")
    if has_open:
        raise PlanRequestError("PLAN_REQUEST_OPEN", "a request is already waiting for an answer")
    clean = (note or "").strip()
    if len(clean) > MAX_NOTE:
        raise PlanRequestError(
            "VALIDATION_FAILED", f"the note is longer than {MAX_NOTE} characters"
        )
    return clean


def check_answer(status: str, answer: str) -> None:
    """Only an open request is answered, and only with an allowed answer."""
    if status != OPEN:
        raise PlanRequestError("PLAN_REQUEST_CLOSED", f"the request is {status.lower()}")
    if answer not in ANSWERS:
        raise PlanRequestError("VALIDATION_FAILED", "answer ACCEPTED or DECLINED")


def check_withdraw(status: str) -> None:
    if status != OPEN:
        raise PlanRequestError("PLAN_REQUEST_CLOSED", f"the request is {status.lower()}")
