"""Professional eligibility, trust dimensions and engagement checks (ZID-GAP-001 v2.0 GID-041, 048, 049). Pure domain.

Eligibility is a POLICY decision for one service, in one place, on one date, made from verified assertions. It is never a field
of a credential and never carried across places:
- a license verified in one country or state says nothing about another (GLB-01, GLB-17, GLB-31);
- a service the license does not list is not permitted, even to a person who holds the license (GLB-31);
- an assertion that is not VERIFIED and ACTIVE, or that has expired, contributes nothing (GLB-04);
- a foreign award where recognition is required is REVIEW, never equivalent (GLB-09);
- missing or stale evidence for a mandatory dimension is a HOLD or BLOCK, never a silent pass: a Tier A badge cannot bypass a
  check the policy configures (GID-048, GLB-19);
- every outcome carries reason codes so it can be appealed; an adverse outcome never rests on a fraud score alone.

Dimensions (Zoikorum, section 10.1) stay separate: IDENTITY, CREDENTIAL, ELIGIBILITY, RESTRICTIONS, INSURANCE.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from zid_platform.modules.credential.domain.claims import Eligibility


class Dimension(StrEnum):
    IDENTITY = "IDENTITY"
    CREDENTIAL = "CREDENTIAL"
    ELIGIBILITY = "ELIGIBILITY"
    RESTRICTIONS = "RESTRICTIONS"
    INSURANCE = "INSURANCE"


class DimStatus(StrEnum):
    VERIFIED = "VERIFIED"
    NOT_VERIFIED = "NOT_VERIFIED"
    EXPIRED = "EXPIRED"
    NOT_REQUIRED = "NOT_REQUIRED"


@dataclass(frozen=True, slots=True)
class EligibilityPolicy:
    policy_id: str
    version: int
    country: str
    subdivision: str | None
    profession_code: str
    service_code: str
    required_claim_types: tuple[str, ...] = ("PROFESSIONAL_LICENSE",)
    recognition_required_for: tuple[
        str, ...
    ] = ()  # claim types whose foreign awards need recognition (academic)
    max_assertion_age_days: int = 365


@dataclass(frozen=True, slots=True)
class AssertionFact:
    """The subset of a stored assertion that eligibility reads. Built from `Assertion.to_contract()` plus the dates."""

    claim_type: str
    decision: str
    credential_status: str
    jurisdiction: Mapping[str, str]
    permitted_services: tuple[str, ...] = ()
    restrictions: tuple[str, ...] = ()
    source_checked_at: datetime | None = None
    expires_at: datetime | None = None
    recognition: str | None = (
        None  # NOT_REQUIRED | REVIEW | UNRECOGNIZED for an award earned elsewhere
    )


@dataclass(frozen=True, slots=True)
class EligibilityOutcome:
    eligibility: Eligibility
    reasons: tuple[str, ...]
    restrictions: tuple[str, ...] = ()
    policy_ref: Mapping[str, Any] = field(default_factory=dict)


def _live(a: AssertionFact, policy: EligibilityPolicy, now: datetime) -> str | None:
    """Why this assertion cannot count, or None if it can."""
    if a.decision != "VERIFIED":
        return f"{a.claim_type}_NOT_VERIFIED"
    if a.credential_status != "ACTIVE":
        return f"{a.claim_type}_{a.credential_status}"
    if a.expires_at is not None and now >= a.expires_at:
        return f"{a.claim_type}_EXPIRED"
    if (
        a.source_checked_at is not None
        and (now - a.source_checked_at).days > policy.max_assertion_age_days
    ):
        return f"{a.claim_type}_STALE"
    return None


def _in_place(a: AssertionFact, policy: EligibilityPolicy) -> bool:
    if a.jurisdiction.get("country", "").upper() != policy.country.upper():
        return False
    return (
        policy.subdivision is None
        or a.jurisdiction.get("subdivision", "").upper() == policy.subdivision.upper()
    )


def evaluate(
    policy: EligibilityPolicy, assertions: Sequence[AssertionFact], *, now: datetime
) -> EligibilityOutcome:
    """ELIGIBLE only when every required claim type has a live, in-place assertion whose license lists this service."""
    ref = {"id": policy.policy_id, "version": policy.version}
    blocked: list[str] = []
    review: list[str] = []
    restrictions: list[str] = []
    for ctype in policy.required_claim_types:
        candidates = [a for a in assertions if a.claim_type == ctype]
        if not candidates:
            blocked.append(f"{ctype}_MISSING")
            continue
        good = []
        for a in candidates:
            why = _live(a, policy, now)
            if why is not None:
                continue
            if ctype in policy.recognition_required_for and a.recognition not in (
                None,
                "NOT_REQUIRED",
            ):
                review.append(f"{ctype}_RECOGNITION_{a.recognition}")  # never auto-equivalent
                continue
            if ctype == "PROFESSIONAL_LICENSE" and not _in_place(a, policy):
                continue  # right credential, wrong place: UK status never implies a US state
            if ctype == "PROFESSIONAL_LICENSE" and policy.service_code not in a.permitted_services:
                continue  # holds the license, not for this service
            good.append(a)
        if good:
            restrictions.extend(r for a in good for r in a.restrictions)
            continue
        if any(ctype in r for r in review):
            continue
        # nothing counted: say the most useful reason, in the order a person would fix them
        reasons = [_live(a, policy, now) for a in candidates]
        if any(r is None for r in reasons):
            in_place = [a for a in candidates if _live(a, policy, now) is None]
            if ctype == "PROFESSIONAL_LICENSE" and not any(_in_place(a, policy) for a in in_place):
                blocked.append(f"{ctype}_WRONG_JURISDICTION")
            else:
                blocked.append(f"{ctype}_SERVICE_NOT_PERMITTED")
        else:
            blocked.append(str(reasons[0]))
    if blocked:
        return EligibilityOutcome(Eligibility.BLOCKED, tuple(blocked + review), (), ref)
    if review:
        return EligibilityOutcome(Eligibility.REVIEW, tuple(review), (), ref)
    return EligibilityOutcome(Eligibility.ELIGIBLE, (), tuple(dict.fromkeys(restrictions)), ref)


# -------------------------------------------------------------------------------------------- trust tiers (GID-048)


class Tier(StrEnum):
    A = "A"
    B = "B"
    C = "C"
    NONE = "NONE"


@dataclass(frozen=True, slots=True)
class TierRule:
    """Which dimensions a tier needs. `mandatory` are required for every tier at or above B (configured per Zoikorum rule)."""

    rule_id: str
    version: int
    tier_a: tuple[Dimension, ...]
    tier_b: tuple[Dimension, ...]
    tier_c: tuple[Dimension, ...] = (Dimension.IDENTITY,)
    mandatory: tuple[Dimension, ...] = ()


def tier_of(rule: TierRule, dims: Mapping[Dimension, DimStatus]) -> tuple[Tier, tuple[str, ...]]:
    """The highest tier whose every required dimension is VERIFIED (NOT_REQUIRED counts as met, but never for a
    mandatory dimension). Expiry or revocation of any needed dimension downgrades at once."""

    def met(dim: Dimension) -> bool:
        status = dims.get(dim, DimStatus.NOT_VERIFIED)
        if dim in rule.mandatory:
            return status is DimStatus.VERIFIED
        return status in (DimStatus.VERIFIED, DimStatus.NOT_REQUIRED)

    # a mandatory dimension that is not verified caps the result below A and B, whatever else is true
    mandatory_gaps = [d for d in rule.mandatory if not met(d)]
    result = Tier.NONE
    for tier, needs in ((Tier.A, rule.tier_a), (Tier.B, rule.tier_b), (Tier.C, rule.tier_c)):
        if tier is not Tier.C and mandatory_gaps:
            continue
        if all(met(d) for d in needs):
            result = tier
            break
    if result is Tier.A:
        return result, ()
    blocking = list(dict.fromkeys(mandatory_gaps + [d for d in rule.tier_a if not met(d)]))
    return result, tuple(f"{d.value}_{dims.get(d, DimStatus.NOT_VERIFIED).value}" for d in blocking)


# -------------------------------------------------------------------------------------- engagement check (GID-049)


class Engagement(StrEnum):
    READY = "READY"
    HOLD = "HOLD"  # re-run needed or evidence missing; the contract is held, nothing is denied for good
    BLOCKED = "BLOCKED"


@dataclass(frozen=True, slots=True)
class EngagementRequest:
    country: str
    subdivision: str | None
    profession_code: str
    service_code: str
    insurance_required: bool = True


@dataclass(frozen=True, slots=True)
class EngagementOutcome:
    engagement: Engagement
    reasons: tuple[str, ...]
    eligibility: Eligibility


def engagement_check(
    request: EngagementRequest,
    policy: EligibilityPolicy,
    assertions: Sequence[AssertionFact],
    *,
    identity_verified: bool,
    insurance_satisfied: bool | None,
    restricted: bool,
    now: datetime,
) -> EngagementOutcome:
    """Evaluate credential + role + risk + jurisdiction + insurance for ONE proposed piece of work. The permission is
    recomputed against the policy for THIS place and service every time; a result for another place is not reused (GLB-17)."""
    if (
        policy.country.upper(),
        policy.subdivision,
        policy.profession_code,
        policy.service_code,
    ) != (
        request.country.upper(),
        request.subdivision,
        request.profession_code,
        request.service_code,
    ):
        return EngagementOutcome(
            Engagement.HOLD, ("POLICY_DOES_NOT_MATCH_ENGAGEMENT",), Eligibility.NOT_EVALUATED
        )
    outcome = evaluate(policy, assertions, now=now)
    reasons = list(outcome.reasons)
    if not identity_verified:
        reasons.append("IDENTITY_NOT_VERIFIED")
    if restricted:
        reasons.append("RESTRICTION_ON_RECORD")
    if request.insurance_required and insurance_satisfied is not True:
        reasons.append(
            "INSURANCE_NOT_SATISFIED" if insurance_satisfied is False else "INSURANCE_NOT_CHECKED"
        )
    if outcome.eligibility is Eligibility.BLOCKED or restricted or insurance_satisfied is False:
        return EngagementOutcome(Engagement.BLOCKED, tuple(reasons), outcome.eligibility)
    if reasons or outcome.eligibility is not Eligibility.ELIGIBLE:
        return EngagementOutcome(Engagement.HOLD, tuple(reasons), outcome.eligibility)
    return EngagementOutcome(Engagement.READY, (), outcome.eligibility)
