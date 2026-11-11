"""Evidence quality, explainability and signed learning credentials (ZID-GAP-001 v2.0 GID-050, GID-051, GID-059). Pure domain.

What evidence IS decides how far a claim can go, and the person (and the appeal reviewer) is told so in plain words:
- OCR, how a PDF looks, a website match or image metadata are appearance, never authenticity, and never rise above SELF_DECLARED;
- a valid signature or hash proves the document was not altered (DOCUMENT_INTEGRITY); a signature from an issuer the registry
  vouches for proves who issued it (ISSUER_AUTHENTICATED); only an answer from the primary source confirms the facts;
- cryptography proves provenance, not truth and not lawful recognition: a signed badge or sealed EU credential is a CLAIM with
  integrity, never a primary-source confirmation, and never a statutory license (GLB-06, 07, 23, 36).
`explain` turns an outcome into source tier, timestamp, confidence label, methodology and limitations, with no invented numbers.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from zid_platform.modules.credential.domain.claims import (
    SPECS,
    Authenticity,
    ClaimType,
    CredentialStatus,
    EvidenceTier,
)


class EvidenceKind(StrEnum):
    OCR_READ = "OCR_READ"
    DOCUMENT_APPEARANCE = "DOCUMENT_APPEARANCE"
    WEBSITE_MATCH = "WEBSITE_MATCH"
    IMAGE_METADATA = "IMAGE_METADATA"
    HOLDER_STATEMENT = "HOLDER_STATEMENT"
    SIGNATURE_VALID = "SIGNATURE_VALID"
    SIGNATURE_FROM_REGISTERED_ISSUER = "SIGNATURE_FROM_REGISTERED_ISSUER"
    PRIMARY_SOURCE_RESPONSE = "PRIMARY_SOURCE_RESPONSE"


CEILING: Mapping[EvidenceKind, EvidenceTier] = {
    EvidenceKind.OCR_READ: EvidenceTier.SELF_DECLARED,
    EvidenceKind.DOCUMENT_APPEARANCE: EvidenceTier.SELF_DECLARED,
    EvidenceKind.WEBSITE_MATCH: EvidenceTier.SELF_DECLARED,
    EvidenceKind.IMAGE_METADATA: EvidenceTier.SELF_DECLARED,
    EvidenceKind.HOLDER_STATEMENT: EvidenceTier.SELF_DECLARED,
    EvidenceKind.SIGNATURE_VALID: EvidenceTier.DOCUMENT_INTEGRITY,
    EvidenceKind.SIGNATURE_FROM_REGISTERED_ISSUER: EvidenceTier.ISSUER_AUTHENTICATED,
    EvidenceKind.PRIMARY_SOURCE_RESPONSE: EvidenceTier.PRIMARY_SOURCE_CONFIRMED,
}


def tier_for(kinds: Collection[EvidenceKind]) -> EvidenceTier:
    """The highest tier ANY of the evidence supports. Appearance-only evidence stays SELF_DECLARED however much there is."""
    return max((CEILING[k] for k in kinds), default=EvidenceTier.SELF_DECLARED)


PROVES = {
    EvidenceTier.SELF_DECLARED: "that the person or a document says so",
    EvidenceTier.DOCUMENT_INTEGRITY: "that the document has not been altered since it was signed",
    EvidenceTier.ISSUER_AUTHENTICATED: "which registered issuer signed it",
    EvidenceTier.PRIMARY_SOURCE_CONFIRMED: "that the source itself confirmed these fields at the time shown",
}
DOES_NOT_PROVE = {
    EvidenceTier.SELF_DECLARED: "a degree, an issuer, a license or any right to practice",
    EvidenceTier.DOCUMENT_INTEGRITY: "that what it says is true, or that the signer is a legitimate source",
    EvidenceTier.ISSUER_AUTHENTICATED: "that this issuer was authorized for this exact award or license, or that it is still valid",
    EvidenceTier.PRIMARY_SOURCE_CONFIRMED: "right to practice elsewhere, or good standing after the time shown",
}
CONFIDENCE = {
    EvidenceTier.SELF_DECLARED: "LOW",
    EvidenceTier.DOCUMENT_INTEGRITY: "LOW",
    EvidenceTier.ISSUER_AUTHENTICATED: "MEDIUM",
    EvidenceTier.PRIMARY_SOURCE_CONFIRMED: "HIGH",
}


@dataclass(frozen=True, slots=True)
class Explanation:
    source_tier: str
    checked_at: str | None
    confidence: str
    methodology: str
    proves: str
    limitations: tuple[str, ...]
    summary: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_tier": self.source_tier,
            "checked_at": self.checked_at,
            "confidence": self.confidence,
            "methodology": self.methodology,
            "proves": self.proves,
            "limitations": list(self.limitations),
            "summary": self.summary,
        }


def explain(
    claim: ClaimType,
    tier: EvidenceTier,
    kinds: Collection[EvidenceKind],
    *,
    decision: str,
    checked_at: datetime | None,
) -> Explanation:
    """A human explanation that never claims more than the tier proves. The decision is quoted, not argued."""
    used = (
        ", ".join(sorted(k.value.lower().replace("_", " ") for k in kinds))
        or "no evidence recorded"
    )
    limits = [
        f"Does not prove {DOES_NOT_PROVE[tier]}.",
        f"Taxonomy rule: {SPECS[claim].disallowed_inference}.",
    ]
    if (
        any(k in (EvidenceKind.OCR_READ, EvidenceKind.DOCUMENT_APPEARANCE) for k in kinds)
        and tier < EvidenceTier.PRIMARY_SOURCE_CONFIRMED
    ):
        limits.append(
            "A document's appearance or a text read from it is not confirmation by the issuer."
        )
    return Explanation(
        source_tier=tier.name,
        checked_at=checked_at.isoformat() if checked_at else None,
        confidence=CONFIDENCE[tier],
        methodology=f"Evidence used: {used}.",
        proves=PROVES[tier].capitalize(),
        limitations=tuple(limits),
        summary=f"Decision {decision}. {PROVES[tier].capitalize()}.",
    )


# ---------------------------------------------------------------------------- signed credentials (GID-050 / 051)


class Kind(StrEnum):
    OPEN_BADGE = "OPEN_BADGE"  # 1EdTech Open Badges 3.0 (a W3C VC)
    CLR = "CLR"  # 1EdTech Comprehensive Learner Record 2.0
    EDC = "EDC"  # Europass European Digital Credential for learning
    VC = "VC"  # any other W3C VC


@dataclass(frozen=True, slots=True)
class VcFacts:
    kind: Kind
    proof_valid: bool  # the cryptographic check, done by the existing verifier
    issuer_id: str | None
    issuer_in_registry: bool  # the trust graph vouches for this issuer for this claim
    subject_id: str | None  # credentialSubject.id
    presenter_ref: str | None  # who is presenting it now (holder binding)
    status: CredentialStatus  # from the credential-status endpoint; UNKNOWN if it could not be read
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    seal_valid: bool = False  # EDC: the qualified e-seal checks out


@dataclass(frozen=True, slots=True)
class VcResult:
    integrity: bool
    issuer_authenticity: Authenticity
    holder_bound: bool
    credential_status: CredentialStatus
    tier: EvidenceTier
    accepted_as_claim: bool  # usable as evidence for a claim; never a primary-source confirmation
    reasons: tuple[str, ...]
    statutory_license: bool = False  # always False: no signed learning credential is a license


def evaluate_vc(facts: VcFacts, *, on: datetime) -> VcResult:
    reasons: list[str] = []
    integrity = facts.proof_valid and (facts.kind is not Kind.EDC or facts.seal_valid)
    if not facts.proof_valid:
        reasons.append("PROOF_INVALID")
    if facts.kind is Kind.EDC and not facts.seal_valid:
        reasons.append("SEAL_INVALID")
    authenticity = (
        Authenticity.CONFIRMED
        if integrity and facts.issuer_in_registry
        else Authenticity.NOT_CONFIRMED
        if integrity
        else Authenticity.UNKNOWN
    )
    if integrity and not facts.issuer_in_registry:
        reasons.append("ISSUER_NOT_IN_REGISTRY")  # genuine signature, untrusted issuer (GLB-07)
    bound = bool(facts.subject_id) and facts.subject_id == facts.presenter_ref
    if not bound:
        reasons.append(
            "HOLDER_BINDING_FAILED"
        )  # a copied valid badge belongs to someone else (GLB-06)
    status = facts.status
    if status in (CredentialStatus.REVOKED, CredentialStatus.SUSPENDED):
        reasons.append(f"CREDENTIAL_{status.value}")  # good signature, bad status (GLB-23, GLB-36)
    elif status is CredentialStatus.UNKNOWN:
        reasons.append("STATUS_UNKNOWN")
    if facts.valid_from and on < facts.valid_from:
        reasons.append("NOT_YET_VALID")
        status = CredentialStatus.UNKNOWN
    if facts.valid_until and on >= facts.valid_until:
        reasons.append("PAST_VALID_UNTIL")
        status = CredentialStatus.EXPIRED
    kinds = []
    if integrity:
        kinds.append(EvidenceKind.SIGNATURE_VALID)
        if facts.issuer_in_registry:
            kinds.append(EvidenceKind.SIGNATURE_FROM_REGISTERED_ISSUER)
    tier = tier_for(
        kinds
    )  # never above ISSUER_AUTHENTICATED: only the source itself reaches PRIMARY
    ok = integrity and facts.issuer_in_registry and bound and status is CredentialStatus.ACTIVE
    return VcResult(integrity, authenticity, bound, status, tier, ok, tuple(reasons))


def facts_from_json(doc: Mapping[str, Any], kind: Kind) -> dict[str, Any]:
    """The fields evaluate_vc needs, read from a W3C VC / Open Badge / EDC JSON document. A missing field stays None."""
    issuer = doc.get("issuer")
    subject = doc.get("credentialSubject")
    if isinstance(subject, list):
        subject = subject[0] if subject else None
    return {
        "kind": kind,
        "issuer_id": issuer.get("id")
        if isinstance(issuer, Mapping)
        else issuer
        if isinstance(issuer, str)
        else None,
        "subject_id": subject.get("id") if isinstance(subject, Mapping) else None,
        "valid_from": _when(doc.get("validFrom") or doc.get("issuanceDate")),
        "valid_until": _when(doc.get("validUntil") or doc.get("expirationDate")),
    }


def _when(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
