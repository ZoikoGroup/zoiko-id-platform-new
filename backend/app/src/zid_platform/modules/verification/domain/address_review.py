"""WF-05 proof of address review: a person decides, and an association needs a second person (ZID-DTV-WF-05 §02, A06, A07, A10, §22; CR-115).
Pure domain, no I/O.

A document is first ASSESSED by the rules (`address.assess_proof`: type and age for the market, issuer, dates, address and name comparison). The rules
never decide: a person does (A07: "neither OCR success nor upload means VERIFIED").

    maker finding     CONFIRMED | REQUIRES_MORE_EVIDENCE | NOT_SUPPORTED, with reason codes
    CONFIRMED         ADDRESS_DOCUMENT_REVIEWED PASS at once; the document then waits for a CHECKER (A10 dual approval)
    checker agrees    ADDRESS_ASSOCIATION_CONFIRMED PASS (§02: acceptable document evidence links the person to the address)
    checker disagrees the association is REVIEW; the document review stands as the maker recorded it
    REQUIRES_MORE     ADDRESS_DOCUMENT_REVIEWED REVIEW
    NOT_SUPPORTED     ADDRESS_DOCUMENT_REVIEWED FAIL (never "fraud": §21, A09)

Rules, each with a test: the checker is never the maker; a document the rules found DOES_NOT_SUPPORT cannot be CONFIRMED (ask for another document);
a market with no approved policy cannot be CONFIRMED; a decided step is never redone; no finding or reason says fraud.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from zid_platform.modules.verification.domain.address import Assessment, Support
from zid_platform.modules.verification.domain.assertions import Assertion
from zid_platform.modules.verification.domain.check_cases import Verdict

REASON = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")


class ReviewError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


class Finding(StrEnum):
    CONFIRMED = "CONFIRMED"
    REQUIRES_MORE_EVIDENCE = "REQUIRES_MORE_EVIDENCE"
    NOT_SUPPORTED = "NOT_SUPPORTED"


class Status(StrEnum):
    AWAITING_REVIEW = "AWAITING_REVIEW"
    AWAITING_CHECKER = "AWAITING_CHECKER"
    DONE = "DONE"


@dataclass(frozen=True, slots=True)
class Result:
    status: Status
    assertion: Assertion
    verdict: Verdict
    reason_codes: tuple[str, ...]


def _reasons(reasons: tuple[str, ...]) -> None:
    for r in reasons:
        if not REASON.match(r) or "FRAUD" in r:
            raise ReviewError("REASON_CODE_NOT_ALLOWED", r)


def maker(
    assessment: Assessment,
    finding: Finding,
    reasons: tuple[str, ...],
    *,
    status: Status,
    policy_status: str,
) -> Result:
    if status is not Status.AWAITING_REVIEW:
        raise ReviewError("REVIEW_ALREADY_DECIDED")
    _reasons(reasons)
    if finding is Finding.CONFIRMED:
        if assessment.support is Support.DOES_NOT_SUPPORT:
            raise ReviewError("DOCUMENT_DOES_NOT_SUPPORT", "ask for another document")
        if any(f.code == "NO_POLICY_FOR_MARKET" for f in assessment.findings):
            raise ReviewError("NO_POLICY_FOR_MARKET", "no approved document rule for this market")
        return Result(
            Status.AWAITING_CHECKER, Assertion.ADDRESS_DOCUMENT_REVIEWED, Verdict.PASS, reasons
        )
    if not reasons:
        raise ReviewError("REASON_REQUIRED")
    verdict = Verdict.FAIL if finding is Finding.NOT_SUPPORTED else Verdict.REVIEW
    return Result(Status.DONE, Assertion.ADDRESS_DOCUMENT_REVIEWED, verdict, reasons)


def checker(
    *, status: Status, maker_by: str, checker_by: str, agrees: bool, reasons: tuple[str, ...]
) -> Result:
    if status is not Status.AWAITING_CHECKER:
        raise ReviewError("NOTHING_TO_CHECK")
    if not checker_by.strip() or checker_by == maker_by:
        raise ReviewError("CHECKER_IS_MAKER", "a second, different person checks")
    _reasons(reasons)
    if agrees:
        return Result(Status.DONE, Assertion.ADDRESS_ASSOCIATION_CONFIRMED, Verdict.PASS, reasons)
    if not reasons:
        raise ReviewError("REASON_REQUIRED")
    return Result(Status.DONE, Assertion.ADDRESS_ASSOCIATION_CONFIRMED, Verdict.REVIEW, reasons)


__all__ = ["Finding", "Result", "ReviewError", "Status", "checker", "maker"]
