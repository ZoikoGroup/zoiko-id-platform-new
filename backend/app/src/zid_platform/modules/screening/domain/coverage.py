"""The four result dimensions of a screening case (ZID-DTV-WF-06 section 05, S03, S05, S09; CR-113). Pure domain, no I/O.

A screening answer is never one "AML passed" badge. It is four separate things, and none implies another:

- COVERAGE: which classes of check actually ran, against which exact source snapshots, and how fresh they are. A no-candidate result on one feed says nothing about
  the classes that were not configured.
- MATCH: what the matching found. A similar name is a candidate, never a confirmed identity.
- DISPOSITION: what the business may do. It is a policy decision, separate from what a provider or a list returned, and it is never CLEARED_FOR_PURPOSE while a class the
  policy requires is unscreened, stale or unavailable.
- REVIEW: who decided, and how many people.

The customer sees a minimal summary in words that never say "AML approved", "clean" or "no risk". Restricted facts (the list, the match reasons, the candidate) stay with the reviewer.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum


class Klass(StrEnum):
    SANCTIONS = "SANCTIONS"
    PEP_RCA = "PEP_RCA"
    ADVERSE_MEDIA = "ADVERSE_MEDIA"
    OWNERSHIP_CONTROL = "OWNERSHIP_CONTROL"


class Coverage(StrEnum):
    OFFICIAL_ONLY = "OFFICIAL_ONLY"
    LICENSED_COMPLETE_FOR_CONTRACTED_CLASSES = "LICENSED_COMPLETE_FOR_CONTRACTED_CLASSES"
    PARTIAL = "PARTIAL"
    NOT_CONFIGURED = "NOT_CONFIGURED"
    SOURCE_STALE = "SOURCE_STALE"
    UNAVAILABLE = "UNAVAILABLE"


class Match(StrEnum):
    NO_CANDIDATE = "NO_CANDIDATE"
    POSSIBLE_MATCH = "POSSIBLE_MATCH"
    CONFIRMED_MATCH = "CONFIRMED_MATCH"
    FALSE_POSITIVE = "FALSE_POSITIVE"
    INSUFFICIENT_INFORMATION = "INSUFFICIENT_INFORMATION"


class Disposition(StrEnum):
    CLEARED_FOR_PURPOSE = "CLEARED_FOR_PURPOSE"
    EDD_REQUIRED = "EDD_REQUIRED"
    HOLD_PENDING_REVIEW = "HOLD_PENDING_REVIEW"
    RESTRICTED = "RESTRICTED"
    PROHIBITED = "PROHIBITED"
    UNRESOLVED = "UNRESOLVED"


class Review(StrEnum):
    AUTOMATED = "AUTOMATED"
    REVIEWER_CONFIRMED = "REVIEWER_CONFIRMED"
    DUAL_REVIEWED = "DUAL_REVIEWED"
    LEGAL_SIGNED = "LEGAL_SIGNED"
    EXPIRED = "EXPIRED"


class Tier(StrEnum):
    OFFICIAL = "OFFICIAL"  # tier 1: an official publisher's own list
    LICENSED = "LICENSED"  # tier 2: a contracted provider


@dataclass(frozen=True, slots=True)
class SourceResult:
    """One source's answer for one subject: what it was, which exact snapshot, and what it found. `candidates` is a count; the candidates themselves are restricted."""

    source_id: str
    klass: Klass
    tier: Tier
    snapshot: (
        str | None
    )  # the digest of the exact corpus version checked; None when nothing was checked
    screened_at: datetime | None
    available: bool = True
    candidates: int = 0
    last_validated_at: datetime | None = (
        None  # when this source's corpus was last released after validation (not when a job last ran)
    )


@dataclass(frozen=True, slots=True)
class CoveragePolicy:
    """What a use case requires, per the signed market policy. `max_age` is how old a source's last validated release may be before it no longer counts."""

    required: frozenset[Klass]
    max_age: timedelta
    version: str = "p1"


@dataclass(frozen=True, slots=True)
class ClassCoverage:
    klass: Klass
    coverage: Coverage
    sources: tuple[str, ...]


def _class_coverage(
    klass: Klass, results: Sequence[SourceResult], policy: CoveragePolicy, now: datetime
) -> ClassCoverage:
    mine = [r for r in results if r.klass is klass]
    if not mine:
        return ClassCoverage(klass, Coverage.NOT_CONFIGURED, ())
    ids = tuple(sorted(r.source_id for r in mine))
    if any(not r.available for r in mine):
        return ClassCoverage(klass, Coverage.UNAVAILABLE, ids)
    if any(r.last_validated_at is None or now - r.last_validated_at > policy.max_age for r in mine):
        return ClassCoverage(klass, Coverage.SOURCE_STALE, ids)
    if any(r.snapshot is None or r.screened_at is None for r in mine):
        return ClassCoverage(klass, Coverage.UNAVAILABLE, ids)
    if all(r.tier is Tier.LICENSED for r in mine):
        return ClassCoverage(klass, Coverage.LICENSED_COMPLETE_FOR_CONTRACTED_CLASSES, ids)
    return ClassCoverage(klass, Coverage.OFFICIAL_ONLY, ids)


def classes(
    results: Sequence[SourceResult], policy: CoveragePolicy, now: datetime
) -> dict[Klass, ClassCoverage]:
    return {k: _class_coverage(k, results, policy, now) for k in Klass}


_GOOD = frozenset({Coverage.OFFICIAL_ONLY, Coverage.LICENSED_COMPLETE_FOR_CONTRACTED_CLASSES})


def overall_coverage(by_class: Mapping[Klass, ClassCoverage], policy: CoveragePolicy) -> Coverage:
    """One word for the whole case, worst first. It is complete only when every class the POLICY requires was checked and fresh; a class the policy does not require, left
    unconfigured, does not lower it, but is still shown as NOT_CONFIGURED in the per-class view so nothing implies it was checked."""
    required = [by_class[k].coverage for k in sorted(policy.required)]
    if not required:
        return Coverage.NOT_CONFIGURED
    for bad in (Coverage.UNAVAILABLE, Coverage.SOURCE_STALE):
        if bad in required:
            return bad
    if all(c is Coverage.NOT_CONFIGURED for c in required):
        return Coverage.NOT_CONFIGURED
    if any(c is Coverage.NOT_CONFIGURED for c in required):
        return Coverage.PARTIAL
    if all(c is Coverage.LICENSED_COMPLETE_FOR_CONTRACTED_CLASSES for c in required):
        return Coverage.LICENSED_COMPLETE_FOR_CONTRACTED_CLASSES
    return Coverage.OFFICIAL_ONLY


_HOLDING = frozenset(
    {Klass.SANCTIONS, Klass.OWNERSHIP_CONTROL}
)  # a candidate in these classes holds the case; PEP and media candidates route to EDD instead


def determine_match(
    results: Sequence[SourceResult],
    *,
    reviewed: Match | None = None,
    insufficient_identifiers: bool = False,
) -> Match:
    """The match state. A reviewer's finding stands once made. Without one, any candidate is only POSSIBLE_MATCH, and missing secondary identifiers make a candidate
    INSUFFICIENT_INFORMATION: they lower certainty and are never negative evidence. NO_CANDIDATE applies only to what was checked."""
    if reviewed is not None:
        return reviewed
    if any(r.candidates > 0 for r in results if r.klass in _HOLDING):
        return Match.INSUFFICIENT_INFORMATION if insufficient_identifiers else Match.POSSIBLE_MATCH
    return Match.NO_CANDIDATE


@dataclass(frozen=True, slots=True)
class Decision:
    disposition: Disposition
    reasons: tuple[str, ...]


def decide_disposition(
    *,
    coverage: Coverage,
    match: Match,
    by_class: Mapping[Klass, ClassCoverage],
    policy: CoveragePolicy,
    legal_signed: bool = False,
    pep_candidate: bool = False,
    media_candidate: bool = False,
) -> Decision:
    """What the business may do. The order matters: a sanctions candidate is looked at before coverage, a gap in coverage stops a clearance, and only then can a case clear.

    CONFIRMED_MATCH is RESTRICTED; it is PROHIBITED only when Legal has signed that determination. A PEP or adverse-media candidate is EDD or a relevance review, never a refusal."""
    reasons: list[str] = []
    if match is Match.CONFIRMED_MATCH:
        if legal_signed:
            return Decision(Disposition.PROHIBITED, ("CONFIRMED_MATCH_LEGALLY_SIGNED",))
        return Decision(Disposition.RESTRICTED, ("CONFIRMED_MATCH",))
    if match is Match.POSSIBLE_MATCH:
        return Decision(Disposition.HOLD_PENDING_REVIEW, ("POSSIBLE_MATCH_AWAITS_REVIEWER",))
    if match is Match.INSUFFICIENT_INFORMATION:
        return Decision(Disposition.UNRESOLVED, ("INSUFFICIENT_IDENTIFIERS",))
    if coverage is Coverage.UNAVAILABLE:
        return Decision(Disposition.UNRESOLVED, ("REQUIRED_SOURCE_UNAVAILABLE",))
    if coverage is Coverage.SOURCE_STALE:
        return Decision(Disposition.UNRESOLVED, ("REQUIRED_SOURCE_STALE",))
    if coverage in (Coverage.NOT_CONFIGURED, Coverage.PARTIAL):
        missing = sorted(k.value for k in policy.required if by_class[k].coverage not in _GOOD)
        return Decision(
            Disposition.UNRESOLVED,
            ("UNSCREENED_REQUIRED_CLASSES", *(f"NOT_SCREENED:{m}" for m in missing)),
        )
    if pep_candidate:
        reasons.append("PEP_OR_RCA_CANDIDATE")
    if media_candidate:
        reasons.append("ADVERSE_MEDIA_RELEVANCE_REVIEW")
    if reasons:
        return Decision(Disposition.EDD_REQUIRED, tuple(reasons))
    reasons.append("NO_CANDIDATE_IN_CHECKED_SOURCES")
    return Decision(Disposition.CLEARED_FOR_PURPOSE, tuple(reasons))


def review_state(
    approvers: Iterable[str], *, legal_signed: bool, expired: bool, human: bool
) -> Review:
    if expired:
        return Review.EXPIRED
    if legal_signed:
        return Review.LEGAL_SIGNED
    people = set(approvers)
    if len(people) >= 2:
        return Review.DUAL_REVIEWED
    if human and people:
        return Review.REVIEWER_CONFIRMED
    return Review.AUTOMATED


def result_model(
    *,
    case_id: str,
    results: Sequence[SourceResult],
    policy: CoveragePolicy,
    now: datetime,
    reviewed_match: Match | None = None,
    insufficient_identifiers: bool = False,
    approvers: Iterable[str] = (),
    legal_signed: bool = False,
    expired: bool = False,
    pep_candidate: bool = False,
    media_candidate: bool = False,
    rule_version: str = "v1",
) -> dict[str, object]:
    """The canonical response of section 11. Per-class coverage is always present, so an unconfigured class reads NOT_CONFIGURED with a null result, never clear."""
    by_class = classes(results, policy, now)
    pep_candidate = pep_candidate or any(
        r.candidates > 0 for r in results if r.klass is Klass.PEP_RCA
    )
    media_candidate = media_candidate or any(
        r.candidates > 0 for r in results if r.klass is Klass.ADVERSE_MEDIA
    )
    coverage = overall_coverage(by_class, policy)
    match = determine_match(
        results, reviewed=reviewed_match, insufficient_identifiers=insufficient_identifiers
    )
    decision = decide_disposition(
        coverage=coverage,
        match=match,
        by_class=by_class,
        policy=policy,
        legal_signed=legal_signed,
        pep_candidate=pep_candidate,
        media_candidate=media_candidate,
    )
    people = tuple(approvers)

    def klass_view(k: Klass) -> dict[str, object]:
        c = by_class[k]
        return {
            "coverage": c.coverage.value,
            "result": None
            if c.coverage in (Coverage.NOT_CONFIGURED, Coverage.UNAVAILABLE)
            else _klass_match(k, results).value,
        }

    return {
        "case_id": case_id,
        "coverage_status": coverage.value,
        "source_results": [
            {
                "source_id": r.source_id,
                "snapshot": r.snapshot,
                "match_status": (Match.POSSIBLE_MATCH if r.candidates else Match.NO_CANDIDATE).value
                if r.available and r.snapshot
                else None,
                "screened_at": r.screened_at.isoformat() if r.screened_at else None,
            }
            for r in sorted(results, key=lambda x: x.source_id)
        ],
        "sanctions": klass_view(Klass.SANCTIONS),
        "pep_rca": klass_view(Klass.PEP_RCA),
        "adverse_media": klass_view(Klass.ADVERSE_MEDIA),
        "ownership_control": klass_view(Klass.OWNERSHIP_CONTROL),
        "match_status": match.value,
        "decision": {
            "status": decision.disposition.value,
            "rule_version": rule_version,
            "reason_codes": list(decision.reasons),
        },
        "review": review_state(
            people, legal_signed=legal_signed, expired=expired, human=bool(people)
        ).value,
    }


def _klass_match(klass: Klass, results: Sequence[SourceResult]) -> Match:
    mine = [r for r in results if r.klass is klass]
    return Match.POSSIBLE_MATCH if any(r.candidates for r in mine) else Match.NO_CANDIDATE


# ---- what the customer is told --------------------------------------------------------------------------------------------

FORBIDDEN_PHRASES = (
    "aml approved",
    "aml passed",
    "aml verified",
    "clean",
    "no risk",
    "risk free",
    "cleared of",
    "sanctioned",
    "pep",
    "adverse media found",
    "criminal",
    "guilty",
)

_COVERAGE_WORDS = {
    Coverage.OFFICIAL_ONLY: "Official sanctions lists checked",
    Coverage.LICENSED_COMPLETE_FOR_CONTRACTED_CLASSES: "Contracted checks completed",
    Coverage.PARTIAL: "Some checks were not run",
    Coverage.NOT_CONFIGURED: "Not enabled",
    Coverage.SOURCE_STALE: "A source needs refreshing",
    Coverage.UNAVAILABLE: "A source is unavailable",
}
_FINDING_WORDS = {
    Disposition.CLEARED_FOR_PURPOSE: "No candidate was found in the checked sources",
    Disposition.EDD_REQUIRED: "Further information is needed",
    Disposition.HOLD_PENDING_REVIEW: "Review pending",
    Disposition.RESTRICTED: "Restricted for this use",
    Disposition.PROHIBITED: "Restricted for this use",
    Disposition.UNRESOLVED: "Further information is needed",
}


def customer_summary(
    model: Mapping[str, object], *, checked_at: datetime | None
) -> dict[str, object]:
    """The minimal status a tenant user may see (S03): per-class coverage, one calibrated finding, and nothing about the list, the candidate or the reason. No-candidate is never
    shown as proof of no risk, and a restricted or prohibited case reads the same as a hold, so the person is not told which list they appeared on."""
    decision = model["decision"]
    assert isinstance(decision, Mapping)
    disposition = Disposition(str(decision["status"]))
    coverage = Coverage(str(model["coverage_status"]))

    def word(key: str) -> str:
        view = model[key]
        assert isinstance(view, Mapping)
        return _COVERAGE_WORDS[Coverage(str(view["coverage"]))]

    return {
        "checked_at": checked_at.isoformat() if checked_at else None,
        "coverage": _COVERAGE_WORDS[coverage],
        "official_sanctions": word("sanctions"),
        "pep_rca": word("pep_rca"),
        "adverse_media": word("adverse_media"),
        "finding": _FINDING_WORDS[disposition],
        "note": "This records what was checked. It says nothing about the person or company beyond that."
        if disposition is Disposition.CLEARED_FOR_PURPOSE
        else "A reviewer will tell you the next step.",
    }


def unsafe_wording(summary: Mapping[str, object]) -> list[str]:
    """Any forbidden phrase in a customer summary, for a test and a guard. Empty is safe."""
    text = " ".join(str(v) for v in summary.values()).casefold()
    return [p for p in FORBIDDEN_PHRASES if p in text]
