"""Whether a screening may be requested, and for which classes (ZID-DTV-WF-06 S01, ENG-01, ENG-02, QA-036; CR-113). Pure domain, no I/O.

The default is off. A request is eligible for a class only when, together: a signed market policy is in force for the tenant's market and purpose, the tenant's data region is one the
policy approves, a lawful basis is recorded, the subject has a current validated identity claim (without one the case waits as PENDING_IDENTITY; it is never a pass), the tenant holds
the right to that class, and the class has a usable source. Every class that is not available says why, in codes, so a screen can say it in words and nothing is hidden by a disabled button.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from zid_platform.modules.screening.domain.coverage import Klass, Tier


class SubjectType(StrEnum):
    INDIVIDUAL = "INDIVIDUAL"
    BUSINESS = "BUSINESS"
    UBO = "UBO"
    REPRESENTATIVE = "REPRESENTATIVE"


@dataclass(frozen=True, slots=True)
class MarketPolicy:
    """A signed policy for one market. Which classes it allows and at which tier; the purposes, regions and subject types it covers; and who signed it and until when."""

    market: str
    version: str
    purposes: frozenset[str]
    regions: frozenset[str]
    subject_types: frozenset[SubjectType]
    classes: Mapping[Klass, Tier]  # the class and the tier the policy lets the tenant use it at
    legal_basis: str | None
    signed_by: tuple[str, ...]
    valid_from: datetime
    valid_to: datetime | None
    evidence_max_age: timedelta = timedelta(days=365)  # how old the subject's identity claim may be

    def in_force(self, now: datetime) -> bool:
        return (
            len(set(self.signed_by)) >= 2
            and self.valid_from <= now
            and (self.valid_to is None or now < self.valid_to)
        )


@dataclass(frozen=True, slots=True)
class IdentityClaim:
    """The WF01, WF02 or WF03 claim the subject rests on: its source and when it was made. The claim, never the person's data."""

    source: str  # e.g. IDENTITY_VERIFIED_FOR_SCOPE, COMPANY_REGISTRY_MATCHED
    assured_at: datetime
    tenant_id: str


@dataclass(frozen=True, slots=True)
class Request:
    tenant_id: str
    market: str
    purpose: str
    region: str
    subject_type: SubjectType
    identity: IdentityClaim | None
    requested: frozenset[Klass]
    subject_ref: str = ""  # the person's account identifier, or the tenant for a company: what the identity claim was read for


@dataclass(frozen=True, slots=True)
class ClassEligibility:
    klass: Klass
    available: bool
    tier: Tier | None
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Eligibility:
    eligible: bool  # at least one requested class is available and nothing blocks the whole request
    blocked: tuple[str, ...]  # why the whole request is refused
    classes: tuple[ClassEligibility, ...]
    pending_identity: bool
    policy_version: str | None
    region: str | None

    def available(self) -> frozenset[Klass]:
        return frozenset(c.klass for c in self.classes if c.available)


def check(
    request: Request,
    policy: MarketPolicy | None,
    *,
    entitled: Collection[Klass],
    usable_sources: Collection[Klass],
    gated_off: Collection[Klass] = (),
    now: datetime,
) -> Eligibility:
    """The whole answer for one request. `entitled` is the classes this tenant holds the right to; `usable_sources` the classes with a configured, fresh source."""
    blocked: list[str] = []
    if policy is None:
        return _refused(request, ("NO_POLICY_FOR_MARKET",), None)
    if policy.market != request.market:
        return _refused(request, ("NO_POLICY_FOR_MARKET",), None)
    if not policy.in_force(now):
        blocked.append(
            "POLICY_NOT_IN_FORCE"
        )  # unsigned (two signatures), not yet effective, or expired
    if request.purpose not in policy.purposes:
        blocked.append("PURPOSE_NOT_APPROVED")
    if request.region not in policy.regions:
        blocked.append("REGION_NOT_APPROVED")
    if request.subject_type not in policy.subject_types:
        blocked.append("SUBJECT_TYPE_NOT_APPROVED")
    if not (policy.legal_basis or "").strip():
        blocked.append("NO_LEGAL_BASIS")
    pending = False
    identity = request.identity
    if identity is None:
        pending = True
        blocked.append("PENDING_IDENTITY")
    elif identity.tenant_id != request.tenant_id:
        pending = True
        blocked.append(
            "IDENTITY_CLAIM_NOT_THIS_TENANTS"
        )  # a claim made for another tenant never carries over
    elif now - identity.assured_at > policy.evidence_max_age:
        pending = True
        blocked.append("IDENTITY_CLAIM_STALE")
    classes: list[ClassEligibility] = []
    for klass in sorted(request.requested):
        reasons: list[str] = list(blocked)
        tier = policy.classes.get(klass)
        if tier is None:
            reasons.append("CLASS_NOT_IN_POLICY")
        if klass not in set(entitled):
            reasons.append("CLASS_NOT_ENTITLED")
        if klass in set(gated_off):
            reasons.append("RELEASE_GATES_NOT_SIGNED")
        if klass not in set(usable_sources):
            reasons.append("NO_USABLE_SOURCE")
        classes.append(
            ClassEligibility(
                klass, not reasons, tier if not reasons else None, tuple(dict.fromkeys(reasons))
            )
        )
    any_available = any(c.available for c in classes)
    return Eligibility(
        eligible=any_available and not blocked,
        blocked=tuple(dict.fromkeys(blocked)),
        classes=tuple(classes),
        pending_identity=pending,
        policy_version=policy.version,
        region=request.region,
    )


def _refused(request: Request, reasons: tuple[str, ...], version: str | None) -> Eligibility:
    return Eligibility(
        False,
        reasons,
        tuple(ClassEligibility(k, False, None, reasons) for k in sorted(request.requested)),
        request.identity is None,
        version,
        None,
    )


_WORDS = {
    "NO_POLICY_FOR_MARKET": "Screening is not enabled for this market.",
    "POLICY_NOT_IN_FORCE": "The screening policy for this market is not in force.",
    "PURPOSE_NOT_APPROVED": "This purpose is not approved for screening in this market.",
    "REGION_NOT_APPROVED": "Your data region is not approved for screening in this market.",
    "SUBJECT_TYPE_NOT_APPROVED": "This kind of subject is not approved for screening in this market.",
    "NO_LEGAL_BASIS": "No legal basis is recorded for screening in this market.",
    "PENDING_IDENTITY": "The person or company needs a current identity check first.",
    "IDENTITY_CLAIM_NOT_THIS_TENANTS": "The identity check was made for a different organization.",
    "IDENTITY_CLAIM_STALE": "The identity check is too old and must be repeated.",
    "CLASS_NOT_IN_POLICY": "This check is not part of the policy for this market.",
    "CLASS_NOT_ENTITLED": "Your organization is not enabled for this check.",
    "NO_USABLE_SOURCE": "This check has no source that is ready.",
    "RELEASE_GATES_NOT_SIGNED": "This check has not been released for your market yet.",
}


def explain(code: str) -> str:
    """A reason in words a customer administrator can read. An unknown code reads as itself."""
    return _WORDS.get(code, code)
