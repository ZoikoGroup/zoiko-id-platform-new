"""Age assurance: threshold assertions without a birth date (ZID-GAP-001 v2.0 DTV-19). Pure domain, no I/O.

A relying party needs "is this person at least 18", not a date of birth. Rules:
- the output is a THRESHOLD result (AT_LEAST, UNDER or UNDETERMINED) with its method and how sure it is; a birth date, an age or an
  estimate never leaves this module;
- a birth date counts only from a verified source: a document or primary-source check. A self-declared date never gives AT_LEAST for a
  threshold (it is UNDETERMINED), because anyone can type anything;
- an age ESTIMATE is a guess with a margin. It decides only when the whole margin is on one side of the threshold plus a safety buffer;
  anywhere near the line it is UNDETERMINED and the person is offered a documented check instead of being refused (uncertainty is
  never a failure, and never a pass);
- a result states how long it can be relied on: AT_LEAST from a verified birth date stays true (age only goes up), UNDER lapses on the
  day the person reaches the threshold, and an estimate lapses within a day.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum

ALLOWED_THRESHOLDS = frozenset({13, 16, 18, 21, 25, 65})
ESTIMATE_BUFFER_YEARS = (
    2.0  # an estimate must clear the line by this much, on top of its own margin
)
ESTIMATE_TTL = timedelta(hours=24)


class AgeError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


class Result(StrEnum):
    AT_LEAST = "AT_LEAST"
    UNDER = "UNDER"
    UNDETERMINED = "UNDETERMINED"


class Method(StrEnum):
    VERIFIED_BIRTH_DATE = "VERIFIED_BIRTH_DATE"
    ESTIMATION = "ESTIMATION"
    SELF_DECLARED = "SELF_DECLARED"


@dataclass(frozen=True, slots=True)
class AgeAssertion:
    threshold: int
    result: Result
    method: Method
    valid_until: datetime | None  # None: stays true for good
    reason_codes: tuple[str, ...] = ()
    fallback_offered: bool = (
        False  # an UNDETERMINED result offers a documented check, never a refusal
    )

    def to_contract(self) -> dict[str, object]:
        return {
            "threshold": self.threshold,
            "result": self.result.value,
            "method": self.method.value,
            "valid_until": self.valid_until.isoformat() if self.valid_until else None,
            "reason_codes": list(self.reason_codes),
            "fallback_offered": self.fallback_offered,
        }


def _check(threshold: int) -> None:
    if threshold not in ALLOWED_THRESHOLDS:
        raise AgeError(
            "AGE_THRESHOLD_NOT_ALLOWED", str(threshold)
        )  # only thresholds a policy defines


def completed_years(born: date, on: date) -> int:
    return on.year - born.year - ((on.month, on.day) < (born.month, born.day))


def birthday(born: date, years: int) -> date:
    try:
        return born.replace(year=born.year + years)
    except ValueError:  # 29 February: the birthday is taken as 1 March in a common year
        return date(born.year + years, 3, 1)


def from_birth_date(born: date, threshold: int, *, on: date, verified: bool) -> AgeAssertion:
    """Exact, but only from a verified source."""
    _check(threshold)
    if not verified:
        return AgeAssertion(
            threshold,
            Result.UNDETERMINED,
            Method.SELF_DECLARED,
            None,
            ("BIRTH_DATE_NOT_VERIFIED",),
            True,
        )
    if born > on:
        raise AgeError("BIRTH_DATE_IN_THE_FUTURE")
    if completed_years(born, on) >= threshold:
        return AgeAssertion(threshold, Result.AT_LEAST, Method.VERIFIED_BIRTH_DATE, None)
    turns = birthday(born, threshold)
    return AgeAssertion(
        threshold,
        Result.UNDER,
        Method.VERIFIED_BIRTH_DATE,
        datetime(turns.year, turns.month, turns.day, tzinfo=UTC),
    )


def from_estimate(
    estimated_age: float, margin: float, threshold: int, *, now: datetime
) -> AgeAssertion:
    """A model's guess: decisive only when the whole margin plus the buffer sits on one side of the line."""
    _check(threshold)
    if margin < 0 or estimated_age < 0:
        raise AgeError("ESTIMATE_INVALID")
    until = now + ESTIMATE_TTL
    if estimated_age - margin >= threshold + ESTIMATE_BUFFER_YEARS:
        return AgeAssertion(threshold, Result.AT_LEAST, Method.ESTIMATION, until)
    if estimated_age + margin < threshold - ESTIMATE_BUFFER_YEARS:
        return AgeAssertion(threshold, Result.UNDER, Method.ESTIMATION, until)
    return AgeAssertion(
        threshold,
        Result.UNDETERMINED,
        Method.ESTIMATION,
        until,
        ("ESTIMATE_TOO_CLOSE_TO_THE_LINE",),
        True,
    )


def combine(assertions: list[AgeAssertion]) -> AgeAssertion:
    """The best evidence wins; a verified birth date beats an estimate. Disagreement is UNDETERMINED, never a pass."""
    if not assertions:
        raise AgeError("NO_EVIDENCE")
    thresholds = {a.threshold for a in assertions}
    if len(thresholds) != 1:
        raise AgeError("MIXED_THRESHOLDS")
    exact = [a for a in assertions if a.method is Method.VERIFIED_BIRTH_DATE]
    if exact:
        return exact[0]
    decided = {a.result for a in assertions if a.result is not Result.UNDETERMINED}
    if len(decided) == 1:
        return next(a for a in assertions if a.result in decided)
    t = thresholds.pop()
    reason = "EVIDENCE_CONFLICTS" if len(decided) > 1 else "NO_DECISIVE_EVIDENCE"
    return AgeAssertion(t, Result.UNDETERMINED, Method.ESTIMATION, None, (reason,), True)


def still_valid(a: AgeAssertion, now: datetime) -> bool:
    return a.valid_until is None or now < a.valid_until
