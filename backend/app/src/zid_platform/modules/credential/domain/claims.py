"""Credential claim taxonomy and the normalized assertion (ZID-GAP-001 v2.0 GID-037; sections 01.1, 04.1, 08.1). Pure domain.

What a claim IS (its type, its fields) is kept apart from how well it is PROVEN (its evidence tier) and from what it
ENTITLES (eligibility). Rules, each with a test:
- an unrecognized claim class fails closed (`CLAIM_CLASS_UNRECOGNIZED`); so does an unknown field, a missing required
  field or an enumerated field with a value the taxonomy does not know;
- evidence tiers say what was proven and nothing more: a document that looks right, an OCR read, a website match or a
  valid signature from an unknown issuer is never primary-source confirmation;
- an assertion carries five separate facts (credential status, issuer authenticity, primary-source verification,
  professional eligibility, consumer entitlement). None is derived from another by the code that reads them;
- "source unavailable" is its own result, never a failure of the person and never a pass;
- an award the source records as ATTENDED is not a conferred degree, and a lapsed, suspended or revoked credential can
  never make anyone eligible.
The decision here is a function of recorded facts only: no clock, no network, no database.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import IntEnum, StrEnum
from typing import Any


class ClaimError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


class ClaimType(StrEnum):
    ACADEMIC_AWARD = "ACADEMIC_AWARD"
    PROFESSIONAL_LICENSE = "PROFESSIONAL_LICENSE"
    INDUSTRY_CERTIFICATE = "INDUSTRY_CERTIFICATE"
    CPD_RECORD = "CPD_RECORD"
    EMPLOYMENT_ASSERTION = "EMPLOYMENT_ASSERTION"
    INSURANCE_ASSERTION = "INSURANCE_ASSERTION"
    DISCIPLINARY_FINDING = "DISCIPLINARY_FINDING"


class EvidenceTier(IntEnum):
    """Section 01.1. Ordered by what was proven; each tier proves only its own sentence."""

    SELF_DECLARED = 0  # a person entered or attested it
    DOCUMENT_INTEGRITY = 1  # a supplied document matches a validated signature or hash
    ISSUER_AUTHENTICATED = 2  # the signature is attributable to an authenticated source
    PRIMARY_SOURCE_CONFIRMED = (
        3  # the academic, regulatory or employer source confirmed fields at a time
    )


# CURRENT_ELIGIBILITY (a policy decision for service, role, region, time) and CONSENTED_PORTABLE_ASSERTION (a scoped
# result shared with a relying party) are not evidence tiers: evidence cannot earn them, so they are separate fields.
TIER_NAMES = {
    "SELF_DECLARED": EvidenceTier.SELF_DECLARED,
    "DOCUMENT_INTEGRITY": EvidenceTier.DOCUMENT_INTEGRITY,
    "ISSUER_AUTHENTICATED": EvidenceTier.ISSUER_AUTHENTICATED,
    "PRIMARY_SOURCE_CONFIRMED": EvidenceTier.PRIMARY_SOURCE_CONFIRMED,
}


class CredentialStatus(StrEnum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    REVOKED = "REVOKED"
    EXPIRED = "EXPIRED"
    UNKNOWN = "UNKNOWN"


class Authenticity(StrEnum):
    CONFIRMED = "CONFIRMED"
    NOT_CONFIRMED = "NOT_CONFIRMED"
    UNKNOWN = "UNKNOWN"


class Eligibility(StrEnum):
    NOT_EVALUATED = "NOT_EVALUATED"
    ELIGIBLE = "ELIGIBLE"
    REVIEW = "REVIEW"
    BLOCKED = "BLOCKED"


class SubjectMatch(StrEnum):
    NOT_CHECKED = "NOT_CHECKED"  # no source was asked, so nobody was compared
    MATCHED = "MATCHED"
    NOT_MATCHED = "NOT_MATCHED"
    AMBIGUOUS = "AMBIGUOUS"


class SourceResult(StrEnum):
    RESPONDED = "RESPONDED"
    UNAVAILABLE = "UNAVAILABLE"  # the source could not be asked or did not answer
    NO_RECORD = "NO_RECORD"  # the source answered and holds nothing for this subject
    NOT_QUERIED = "NOT_QUERIED"  # the check stopped before any source was asked


class Decision(StrEnum):
    VERIFIED = "VERIFIED"
    NOT_VERIFIED = "NOT_VERIFIED"
    REVIEW = "REVIEW"
    SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"
    UNVERIFIABLE = (
        "UNVERIFIABLE"  # no source can confirm it (e.g. a former employer that no longer exists)
    )
    INDETERMINATE = (
        "INDETERMINATE"  # evidence exists but cannot satisfy the requirement (e.g. unapproved CPD)
    )


@dataclass(frozen=True, slots=True)
class ClaimSpec:
    """Fields of one claim class (section 04.1) and the inference the taxonomy refuses."""

    required: frozenset[str]
    optional: frozenset[str] = frozenset()
    enums: Mapping[str, frozenset[str]] = field(default_factory=dict)
    # field -> the only values that may stand behind VERIFIED once a primary source has confirmed them
    verified_requires: Mapping[str, frozenset[str]] = field(default_factory=dict)
    disallowed_inference: str = ""


def _f(*names: str) -> frozenset[str]:
    return frozenset(names)


SPECS: Mapping[ClaimType, ClaimSpec] = {
    ClaimType.ACADEMIC_AWARD: ClaimSpec(
        required=_f("institution", "program", "award_status"),
        optional=_f("campus", "field", "conferral_date", "transcript_ref"),
        enums={"award_status": _f("ATTENDED", "CONFERRED", "WITHDRAWN", "RESCINDED", "UNKNOWN")},
        verified_requires={"award_status": _f("CONFERRED")},
        disallowed_inference="attendance is not an earned degree; a degree is not a professional license",
    ),
    ClaimType.PROFESSIONAL_LICENSE: ClaimSpec(
        required=_f("license_ref", "regulator", "activity_scope"),
        optional=_f("rank", "good_standing", "conditions", "effective_from", "effective_until"),
        enums={"good_standing": _f("GOOD", "CONDITIONS", "NOT_GOOD", "UNKNOWN")},
        disallowed_inference="a prior license is not current practice eligibility",
    ),
    ClaimType.INDUSTRY_CERTIFICATE: ClaimSpec(
        required=_f("issuer", "credential_id"),
        optional=_f("training_track", "test", "renewal_due"),
        disallowed_inference="course completion is not an accredited qualification",
    ),
    ClaimType.CPD_RECORD: ClaimSpec(
        required=_f("credits", "cycle", "evidence_source", "profession_code"),
        optional=_f("attendance"),
        disallowed_inference="participation is not regulator compliance",
    ),
    ClaimType.EMPLOYMENT_ASSERTION: ClaimSpec(
        required=_f("employer_id", "start_date"),
        optional=_f("end_date", "title", "contract_category", "subject_verified"),
        disallowed_inference="a reference letter is not verified employment history",
    ),
    ClaimType.INSURANCE_ASSERTION: ClaimSpec(
        required=_f("insured_entity", "insurer", "coverage_type", "region", "expires_on"),
        optional=_f("activities", "excluded_work"),
        disallowed_inference="company coverage is not personal coverage",
    ),
    ClaimType.DISCIPLINARY_FINDING: ClaimSpec(
        required=_f("regulator", "disposition", "subject_match"),
        optional=_f("finding_date", "appeal_pending", "source"),
        enums={"subject_match": _f("MATCHED", "AMBIGUOUS", "NOT_MATCHED")},
        verified_requires={"subject_match": _f("MATCHED")},
        disallowed_inference="a name-only match is not an adjudicated finding",
    ),
}


# the service code a pack is published under (section 08 scopes); kept here, not where events are emitted
SERVICE_CODES: Mapping[ClaimType, str] = {
    ClaimType.ACADEMIC_AWARD: "academic.verify",
    ClaimType.PROFESSIONAL_LICENSE: "license.verify",
    ClaimType.INDUSTRY_CERTIFICATE: "credential.verify",
    ClaimType.EMPLOYMENT_ASSERTION: "employment.verify",
    ClaimType.CPD_RECORD: "cpd.verify",
    ClaimType.INSURANCE_ASSERTION: "insurance.verify",
}


def claim_type(value: object) -> ClaimType:
    """Fail closed on a claim class the taxonomy does not define (GID-037 negative proof)."""
    try:
        return ClaimType(str(value))
    except ValueError as e:
        raise ClaimError("CLAIM_CLASS_UNRECOGNIZED", str(value)[:64]) from e


def validate_claim(kind: object, fields: Mapping[str, Any]) -> ClaimType:
    """Check a claim's fields against its class. Unknown fields are refused, not ignored."""
    ctype = claim_type(kind)
    spec = SPECS[ctype]
    missing = sorted(spec.required - fields.keys())
    if missing:
        raise ClaimError("CLAIM_FIELD_MISSING", ",".join(missing))
    unknown = sorted(set(fields) - spec.required - spec.optional)
    if unknown:
        raise ClaimError("CLAIM_FIELD_UNKNOWN", ",".join(unknown))
    for name, allowed in spec.enums.items():
        if name in fields and fields[name] not in allowed:
            raise ClaimError("CLAIM_VALUE_UNRECOGNIZED", name)
    return ctype


def tier(value: object) -> EvidenceTier:
    """Parse a tier name. Eligibility and portability are not evidence tiers and are refused here."""
    try:
        return TIER_NAMES[str(value)]
    except KeyError as e:
        raise ClaimError("EVIDENCE_TIER_UNRECOGNIZED", str(value)[:64]) from e


def decide(
    claim: ClaimType,
    *,
    source_fields: Mapping[str, Any],
    evidence: EvidenceTier,
    source: SourceResult,
    authenticity: Authenticity,
    status: CredentialStatus,
    subject: SubjectMatch,
    source_obtainable: bool = True,
) -> tuple[Decision, tuple[str, ...]]:
    """The one rule for a verification outcome. Returns the decision and its reason codes.

    `source_fields` are the fields the PRIMARY SOURCE confirmed (not what the holder claimed). Nothing the holder or a
    document says can raise the decision above what `evidence` proves.
    """
    spec = SPECS[claim]
    if source is SourceResult.UNAVAILABLE:
        return Decision.SOURCE_UNAVAILABLE, ("SOURCE_UNAVAILABLE",)  # never FAILED, never VERIFIED
    if source is SourceResult.NO_RECORD:
        if not source_obtainable:
            return Decision.UNVERIFIABLE, (
                "NO_SOURCE_CAN_CONFIRM",
            )  # lack of a record is not fraud
        return Decision.NOT_VERIFIED, ("SOURCE_HAS_NO_RECORD",)
    if source is SourceResult.NOT_QUERIED:
        # nobody was asked, so nothing was found: a document alone is "pending or limited", never a negative verdict on the
        # person (ZID-INT-002 section 15 and T06)
        return Decision.INDETERMINATE, ("PRIMARY_SOURCE_NOT_CONFIRMED",)
    if evidence < EvidenceTier.PRIMARY_SOURCE_CONFIRMED:
        # a scan, an OCR read or a valid signature alone: integrity at best, never a source-confirmed pass
        return Decision.NOT_VERIFIED, ("PRIMARY_SOURCE_NOT_CONFIRMED",)
    if authenticity is not Authenticity.CONFIRMED:
        return Decision.NOT_VERIFIED, ("ISSUER_NOT_AUTHENTICATED",)
    if subject is SubjectMatch.AMBIGUOUS:
        return Decision.REVIEW, (
            "SUBJECT_MATCH_AMBIGUOUS",
        )  # a person decides; no adverse result on a guess
    if subject is SubjectMatch.NOT_MATCHED:
        return Decision.NOT_VERIFIED, ("SUBJECT_NOT_MATCHED",)
    for name, allowed in spec.verified_requires.items():
        if source_fields.get(name) not in allowed:
            return Decision.NOT_VERIFIED, (f"{name.upper()}_NOT_{'_OR_'.join(sorted(allowed))}",)
    if status is not CredentialStatus.ACTIVE:
        return Decision.NOT_VERIFIED, (f"CREDENTIAL_{status.value}",)
    return Decision.VERIFIED, ()


@dataclass(frozen=True, slots=True)
class Assertion:
    """Section 08.1. Five separate facts; a value in one never implies a value in another."""

    claim_type: ClaimType
    decision: Decision
    credential_status: CredentialStatus
    issuer_authenticity: Authenticity
    primary_source_verified: bool
    professional_eligibility: Eligibility
    current_consumer_entitlement: Eligibility
    assurance_tier: EvidenceTier
    subject_match: SubjectMatch
    reason_codes: tuple[str, ...] = ()
    jurisdiction: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        verified = self.decision is Decision.VERIFIED
        if verified and not (
            self.primary_source_verified
            and self.assurance_tier >= EvidenceTier.PRIMARY_SOURCE_CONFIRMED
            and self.issuer_authenticity is Authenticity.CONFIRMED
            and self.credential_status is CredentialStatus.ACTIVE
            and self.subject_match is SubjectMatch.MATCHED
        ):
            raise ClaimError("ASSERTION_VERIFIED_WITHOUT_BASIS")
        if (
            self.primary_source_verified
            and self.assurance_tier < EvidenceTier.PRIMARY_SOURCE_CONFIRMED
        ):
            raise ClaimError("ASSERTION_TIER_CONTRADICTS_SOURCE")
        for elig in (self.professional_eligibility, self.current_consumer_entitlement):
            if elig is Eligibility.ELIGIBLE and not verified:
                raise ClaimError("ASSERTION_ELIGIBLE_WITHOUT_VERIFIED_CLAIM")
        if self.current_consumer_entitlement is Eligibility.ELIGIBLE and (
            self.professional_eligibility is not Eligibility.ELIGIBLE
        ):
            raise ClaimError("ASSERTION_ENTITLEMENT_WITHOUT_ELIGIBILITY")

    def to_contract(self) -> dict[str, Any]:
        """The 08.1 shape for the facts this slice owns. Identity, policy, consent and signature are added by callers."""
        return {
            "claim_type": self.claim_type.value,
            "decision": self.decision.value,
            "credential_status": self.credential_status.value,
            "issuer_authenticity": self.issuer_authenticity.value,
            "primary_source_verified": self.primary_source_verified,
            "professional_eligibility": self.professional_eligibility.value,
            "current_consumer_entitlement": self.current_consumer_entitlement.value,
            "assurance_tier": self.assurance_tier.name,
            "subject_match": {"decision": self.subject_match.value},
            "jurisdiction": dict(self.jurisdiction),
            "reason_codes": list(self.reason_codes),
        }


def assertion_from_facts(
    claim: ClaimType,
    *,
    source_fields: Mapping[str, Any],
    evidence: EvidenceTier,
    source: SourceResult,
    authenticity: Authenticity,
    status: CredentialStatus,
    subject: SubjectMatch,
    source_obtainable: bool = True,
    jurisdiction: Mapping[str, str] | None = None,
) -> Assertion:
    """Decide, then build the assertion. Eligibility stays NOT_EVALUATED: it is a separate policy decision."""
    decision, reasons = decide(
        claim,
        source_fields=source_fields,
        evidence=evidence,
        source=source,
        authenticity=authenticity,
        status=status,
        subject=subject,
        source_obtainable=source_obtainable,
    )
    return Assertion(
        claim_type=claim,
        decision=decision,
        credential_status=status,
        issuer_authenticity=authenticity,
        primary_source_verified=(
            source is SourceResult.RESPONDED and evidence >= EvidenceTier.PRIMARY_SOURCE_CONFIRMED
        ),
        professional_eligibility=Eligibility.NOT_EVALUATED,
        current_consumer_entitlement=Eligibility.NOT_EVALUATED,
        assurance_tier=evidence,
        subject_match=subject,
        reason_codes=reasons,
        jurisdiction=dict(jurisdiction or {}),
    )


def unqueried_assertion(
    claim: ClaimType,
    decision: Decision,
    reasons: tuple[str, ...],
    *,
    authenticity: Authenticity = Authenticity.UNKNOWN,
    jurisdiction: Mapping[str, str] | None = None,
) -> Assertion:
    """An assertion for a check that stopped before any source was asked (an unknown or lookalike issuer, a refused
    contract). It proves nothing: self-declared, unchecked, never VERIFIED."""
    if decision is Decision.VERIFIED:
        raise ClaimError("ASSERTION_VERIFIED_WITHOUT_BASIS")
    return Assertion(
        claim_type=claim,
        decision=decision,
        credential_status=CredentialStatus.UNKNOWN,
        issuer_authenticity=authenticity,
        primary_source_verified=False,
        professional_eligibility=Eligibility.NOT_EVALUATED,
        current_consumer_entitlement=Eligibility.NOT_EVALUATED,
        assurance_tier=EvidenceTier.SELF_DECLARED,
        subject_match=SubjectMatch.NOT_CHECKED,
        reason_codes=reasons,
        jurisdiction=dict(jurisdiction or {}),
    )
