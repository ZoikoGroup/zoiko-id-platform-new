"""WF-05 verification cases and their evidence assertions (ZID-DTV-WF-05 §02, §06, §19, §20, §21, A01, A09; CR-112). Pure domain, no I/O.

A case is one request by a tenant to check a subject's contact, address or device evidence, for one purpose and market, processed in one region, under
the eligibility answer it was opened with (`policy_hash`). Its evidence is a list of assertions, each kept apart (§02): there is no single "verified",
no overall green, and no score.

Case states (§06): CREATED, ELIGIBLE, IN_PROGRESS, REVIEW, COMPLETE, CLOSED. A case opens ELIGIBLE when at least one method it asks for is available, and
CREATED (with the reasons) when none is: "This step is unavailable"; nothing is ever silently opted in (A01). A case keeps the methods it was cleared
for when it opened, so a CREATED case can only be closed: when a method becomes available, a new case is opened under the new answer. CLOSED is final.

Assertion rules, each with a test:
- an assertion comes from a method that can produce it (`check_methods.PRODUCES`) and that was available to THIS case when it opened: evidence from a
  method the case was not cleared for is refused (METHOD_NOT_AVAILABLE_FOR_CASE);
- a verdict is PASS, REVIEW, INDETERMINATE, FAIL or UNAVAILABLE. PASS and FAIL need a source; a non-PASS verdict names at least one reason code;
- no reason code says fraud (§21, A09: "could not confirm" never reads "fraud" without a policy-defined adverse determination, which is not built);
- an assertion can carry an expiry; after it, the evidence shows EXPIRED, never PASS;
- assertions are append-only. A later assertion of the same kind supersedes the earlier one for display, and the earlier one is kept;
- an assertion is never recorded on a CLOSED case.

The evidence view (§19 `GET /v1/verification-cases/{id}/evidence`, A09) lists each assertion kind on its own with its current verdict, source, recency
and what it does NOT prove, and never carries an overall result.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

from zid_platform.modules.verification.domain import check_methods as cm
from zid_platform.modules.verification.domain.assertions import PROVES, Assertion

SUBJECT_REF = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$"
)  # an opaque reference, never an email, phone or name
PURPOSE = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")
COUNTRY = re.compile(r"^[A-Z]{2}$")
REASON_CODE = re.compile(r"^[A-Z][A-Z0-9_:.]{1,63}$")
SOURCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{1,127}$")
FORBIDDEN_REASON_WORDS = ("FRAUD",)
CLOCK_SKEW = timedelta(minutes=5)
MAX_TTL = timedelta(days=3650)


class CaseError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


class CaseStatus(StrEnum):
    CREATED = "CREATED"
    ELIGIBLE = "ELIGIBLE"
    IN_PROGRESS = "IN_PROGRESS"
    REVIEW = "REVIEW"
    COMPLETE = "COMPLETE"
    CLOSED = "CLOSED"


# the moves a case may make; CLOSED is final
MOVES: dict[CaseStatus, frozenset[CaseStatus]] = {
    CaseStatus.CREATED: frozenset(
        {CaseStatus.CLOSED}
    ),  # its methods are fixed at opening: a new case is opened when one becomes available
    CaseStatus.ELIGIBLE: frozenset({CaseStatus.IN_PROGRESS, CaseStatus.CLOSED}),
    CaseStatus.IN_PROGRESS: frozenset({CaseStatus.REVIEW, CaseStatus.COMPLETE, CaseStatus.CLOSED}),
    CaseStatus.REVIEW: frozenset({CaseStatus.IN_PROGRESS, CaseStatus.COMPLETE, CaseStatus.CLOSED}),
    CaseStatus.COMPLETE: frozenset({CaseStatus.CLOSED}),
    CaseStatus.CLOSED: frozenset(),
}


class Verdict(StrEnum):
    PASS = "PASS"
    REVIEW = "REVIEW"
    INDETERMINATE = "INDETERMINATE"
    FAIL = "FAIL"
    UNAVAILABLE = "UNAVAILABLE"


class ConfidenceBand(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


@dataclass(frozen=True, slots=True)
class Case:
    case_id: str
    tenant_id: str
    subject_ref: str
    purpose: str
    market: str
    region: str
    requested: tuple[cm.CheckMethod, ...]
    available: tuple[
        cm.CheckMethod, ...
    ]  # the requested methods that were available when the case opened
    unavailable_reasons: tuple[str, ...]  # why each requested method was not, as METHOD:REASON
    policy_hash: str
    status: CaseStatus
    correlation_id: str
    created_by: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class AssertionRecord:
    assertion_id: str
    case_id: str
    assertion: Assertion
    verdict: Verdict
    method: cm.CheckMethod
    source: str | None  # the adapter (engine@version) or reviewer reference that produced it
    reason_codes: tuple[str, ...]
    confidence: ConfidenceBand | None
    observed_at: datetime
    expires_at: datetime | None
    recorded_by: str
    recorded_at: datetime


# --------------------------------------------------------------------------------------------------------------------------------- opening


def open_case(
    *,
    case_id: str,
    tenant_id: str,
    subject_ref: str,
    purpose: str,
    market: str,
    region: str,
    requested: Sequence[cm.CheckMethod],
    eligibility: Sequence[cm.MethodEligibility],
    policy_hash: str,
    correlation_id: str,
    created_by: str,
    now: datetime,
) -> Case:
    """A new case under the eligibility answer it opens with. ELIGIBLE when a requested method is available; CREATED (and why) when none is."""
    if not SUBJECT_REF.match(subject_ref) or "@" in subject_ref:
        raise CaseError(
            "SUBJECT_REF_INVALID", "an opaque reference, never an email or a phone number"
        )
    if not PURPOSE.match(purpose):
        raise CaseError("PURPOSE_INVALID", purpose)
    if not COUNTRY.match(market):
        raise CaseError("MARKET_INVALID", market)
    if not region.strip():
        raise CaseError("REGION_REQUIRED")
    if not requested:
        raise CaseError("METHODS_REQUIRED", "name the checks the case asks for")
    if len(set(requested)) != len(requested):
        raise CaseError("METHODS_DUPLICATED")
    if now.tzinfo is None:
        raise CaseError("TIME_INVALID", "times carry a time zone")
    by_method = {e.method: e for e in eligibility}
    available: list[cm.CheckMethod] = []
    reasons: list[str] = []
    for m in requested:
        e = by_method.get(m)
        if e is not None and e.available:
            available.append(m)
        else:
            for r in e.reasons if e is not None else ("NOT_ASSESSED",):
                reasons.append(f"{m.value}:{r}")
    status = CaseStatus.ELIGIBLE if available else CaseStatus.CREATED
    return Case(
        case_id=case_id, tenant_id=tenant_id, subject_ref=subject_ref, purpose=purpose, market=market, region=region,
        requested=tuple(requested), available=tuple(available), unavailable_reasons=tuple(reasons), policy_hash=policy_hash,
        status=status, correlation_id=correlation_id, created_by=created_by, created_at=now, updated_at=now,
    )  # fmt: skip


def move(case: Case, to: CaseStatus, *, now: datetime) -> Case:
    if to not in MOVES[case.status]:
        raise CaseError("CASE_MOVE_NOT_ALLOWED", f"{case.status.value} to {to.value}")
    return Case(**{**_fields(case), "status": to, "updated_at": now})


def _fields(case: Case) -> dict[str, Any]:
    return {f: getattr(case, f) for f in Case.__dataclass_fields__}


# --------------------------------------------------------------------------------------------------------------------------------- assertions


def check_assertion(case: Case, a: AssertionRecord, *, now: datetime) -> None:
    """Refuse an assertion the case may not hold. Every refusal is named."""
    if a.case_id != case.case_id:
        raise CaseError("ASSERTION_CASE_MISMATCH")
    if case.status is CaseStatus.CLOSED:
        raise CaseError("CASE_CLOSED", "a closed case takes no new evidence")
    try:
        cm.check_produces(a.method, a.assertion)
    except cm.CheckMethodError as e:
        raise CaseError(e.code, str(e)) from e
    if a.method not in case.available:
        raise CaseError(
            "METHOD_NOT_AVAILABLE_FOR_CASE",
            f"{a.method.value} was not available when the case opened",
        )
    if a.verdict in (Verdict.PASS, Verdict.FAIL) and not (a.source and SOURCE.match(a.source)):
        raise CaseError("SOURCE_REQUIRED", "a PASS or FAIL names what produced it")
    if a.source is not None and not SOURCE.match(a.source):
        raise CaseError("SOURCE_INVALID")
    if a.verdict is not Verdict.PASS and not a.reason_codes:
        raise CaseError("REASON_REQUIRED", "say why it is not a PASS")
    for code in a.reason_codes:
        if not REASON_CODE.match(code):
            raise CaseError("REASON_CODE_INVALID", code)
        if any(word in code for word in FORBIDDEN_REASON_WORDS):
            raise CaseError(
                "REASON_CODE_NOT_ALLOWED",
                "evidence that could not be confirmed is never called fraud (WF-05 §21, A09)",
            )
    for t in (a.observed_at, a.recorded_at):
        if t.tzinfo is None:
            raise CaseError("TIME_INVALID", "times carry a time zone")
        if t > now + CLOCK_SKEW:
            raise CaseError("FROM_THE_FUTURE")
    if a.expires_at is not None and not a.observed_at < a.expires_at <= a.observed_at + MAX_TTL:
        raise CaseError("EXPIRY_INVALID", "an expiry is after the observation, and not unbounded")
    if not a.recorded_by.strip():
        raise CaseError("ACTOR_REQUIRED")


def evidence_view(
    case: Case, records: Iterable[AssertionRecord], *, now: datetime
) -> dict[str, Any]:
    """Each assertion kind on its own: the latest record of each, its status, source and recency, and what it does not prove. No overall result."""
    latest: dict[Assertion, AssertionRecord] = {}
    history: dict[Assertion, int] = {}
    for rec in sorted(records, key=lambda x: (x.recorded_at, x.assertion_id)):
        latest[rec.assertion] = rec
        history[rec.assertion] = history.get(rec.assertion, 0) + 1
    items = []
    for kind in Assertion:
        r = latest.get(kind)
        if r is None:
            continue
        expired = r.expires_at is not None and r.expires_at <= now
        items.append(
            {
                "type": kind.value,
                "outcome": "EXPIRED" if expired else r.verdict.value,
                "method": r.method.value,
                "source": r.source,
                "reason_codes": list(r.reason_codes) + (["EVIDENCE_EXPIRED"] if expired else []),
                "confidence": r.confidence.value if r.confidence else None,
                "observed_at": r.observed_at.isoformat(),
                "expires_at": r.expires_at.isoformat() if r.expires_at else None,
                "earlier_records": history[kind] - 1,
                "proves": PROVES[kind][0],
                "does_not_prove": PROVES[kind][1],
            }
        )
    return {
        "case_id": case.case_id,
        "status": case.status.value,
        "market": case.market,
        "region": case.region,
        "policy_hash": case.policy_hash,
        "assertions": items,
        "not_yet_shown": [
            a.value
            for a in sorted(
                {a for m in case.available for a in cm.PRODUCES[m]} - set(latest), key=str
            )
        ],
        "note": "Each assertion stands alone. There is no overall verified result.",
    }


__all__ = [
    "MOVES",
    "AssertionRecord",
    "Case",
    "CaseError",
    "CaseStatus",
    "ConfidenceBand",
    "Verdict",
    "check_assertion",
    "evidence_view",
    "move",
    "open_case",
]
