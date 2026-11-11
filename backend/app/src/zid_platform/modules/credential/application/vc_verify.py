"""Verify a W3C Verifiable Credential (vc+jwt) as EVIDENCE for a claim (ZID-GAP-001 v2.0 GID-050; section 08 `vc.verify`).

This reuses the existing verifier pieces (issuer keys from the typed trust table, `verify_external_jws`, Bitstring Status List) and
adds no cryptography of its own. The answer is the five separate facts of `evidence_quality.evaluate_vc`:
- integrity: the issuer's signature checks out under the issuer's pinned or federated keys;
- issuer authenticity: the issuer is trusted FOR this credential type (a good signature from an issuer not trusted for the type is
  integrity only, GLB-07);
- holder binding: the caller proved possession of the key the credential is bound to (a holder-signed vp+jwt enveloping this exact
  credential, fresh, for this verifier); a copied credential presented by someone else is not bound (GLB-06);
- status: read from the issuer's own status list; an unreadable status is UNKNOWN, never acceptance (GLB-23);
- the result is a CLAIM with provenance, never a primary-source confirmation and never a license (GLB-36).
EDC (European Digital Credential) e-seals are XML/JSON-LD seals that this path cannot validate, so they are refused, not guessed.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection
from zid_crypto import CryptoError, verify_external_jws

from zid_platform.modules.credential.application import verifier
from zid_platform.modules.credential.domain import vcdm
from zid_platform.modules.credential.domain.claims import ClaimType, CredentialStatus, EvidenceTier
from zid_platform.modules.credential.domain.credentials import (
    CredentialError,
    trust_decision,
)
from zid_platform.modules.credential.domain.evidence_quality import (
    EvidenceKind,
    Explanation,
    Kind,
    VcFacts,
    VcResult,
    evaluate_vc,
    explain,
    facts_from_json,
)
from zid_platform.modules.credential.infrastructure import credential_store as store

OPEN_BADGE_TYPES = frozenset({"OpenBadgeCredential", "AchievementCredential"})
CLR_TYPES = frozenset({"ClrCredential"})
EDC_TYPES = frozenset({"EuropeanDigitalCredential", "EuropassEdc", "EDC"})
STATUS_MAP = {
    "VALID": CredentialStatus.ACTIVE,
    "REVOKED": CredentialStatus.REVOKED,
    "SUSPENDED": CredentialStatus.SUSPENDED,
}


def kind_of(doc: Mapping[str, Any]) -> Kind:
    types = set(vcdm._types(doc))
    if types & EDC_TYPES:
        return Kind.EDC
    if types & CLR_TYPES:
        return Kind.CLR
    if types & OPEN_BADGE_TYPES:
        return Kind.OPEN_BADGE
    return Kind.VC


@dataclass(frozen=True, slots=True)
class VcOutcome:
    kind: Kind
    result: VcResult
    explanation: Explanation
    issuer_id: str | None

    def to_contract(self) -> dict[str, Any]:
        r = self.result
        return {
            "kind": self.kind.value,
            "issuer_ref": self.issuer_id,
            "integrity": r.integrity,
            "issuer_authenticity": r.issuer_authenticity.value,
            "holder_bound": r.holder_bound,
            "credential_status": r.credential_status.value,
            "assurance_tier": r.tier.name,
            "accepted_as_claim": r.accepted_as_claim,
            "primary_source_verified": False,  # a signed credential never is
            "statutory_license": r.statutory_license,
            "reason_codes": list(r.reasons),
            "explanation": self.explanation.as_dict(),
        }


async def verify_vc(
    conn: AsyncConnection,
    settings: verifier.VerifierSettings,
    *,
    vc_jwt: str,
    holder_proof: str | None,
    jurisdiction: str | None,
    now: datetime,
) -> VcOutcome:
    try:
        vc_header, vc_unverified = verifier._jwt_payload(vc_jwt)
    except CredentialError as e:
        raise CredentialError("VC_MALFORMED", e.code) from e
    if vc_header.get("typ") != vcdm.VC_TYP:
        raise CredentialError("VC_MALFORMED", "typ")
    kind = kind_of(vc_unverified)
    if kind is Kind.EDC:
        raise CredentialError("EDC_SEAL_VALIDATION_NOT_AVAILABLE")
    issuer = vcdm.issuer_of(vc_unverified)
    ctype = next(
        (t for t in reversed(vcdm._types(vc_unverified)) if t != "VerifiableCredential"), ""
    )

    proof_valid = False
    in_registry = False
    status = CredentialStatus.UNKNOWN
    vc: dict[str, Any] = {}
    keys: dict[str, Any] = {"keys": []}
    found = await store.load_trust(conn, issuer)
    if found is not None:
        trust, row = found
        try:
            keys = await verifier._issuer_keys(conn, settings, row, now)
            _, vc = verify_external_jws(
                vc_jwt, keys=keys.get("keys", []), algorithms=row["algorithms"], typ=vcdm.VC_TYP
            )
            proof_valid = vcdm.issuer_of(vc) == issuer
        except (CryptoError, CredentialError):
            proof_valid = False
        if proof_valid:
            try:
                trust_decision(trust, credential_type=ctype, jurisdiction=jurisdiction, now=now)
                in_registry = True
            except CredentialError:
                in_registry = False  # a good signature from an issuer not trusted for this type: integrity only
            if "credentialStatus" in vc:
                status = STATUS_MAP.get(
                    await verifier._bitstring_status(
                        settings, vc, issuer, keys, list(row["algorithms"]), now
                    ),
                    CredentialStatus.UNKNOWN,
                )
    doc = vc if proof_valid else vc_unverified
    parsed = facts_from_json(doc, kind)
    bound = False
    if proof_valid and holder_proof:
        bound = _holder_bound(vc, vc_jwt, holder_proof, settings, now)
    facts = VcFacts(
        kind=kind,
        proof_valid=proof_valid,
        issuer_id=issuer,
        issuer_in_registry=in_registry,
        subject_id=parsed["subject_id"],
        presenter_ref=parsed["subject_id"] if bound else None,
        status=status,
        valid_from=parsed["valid_from"],
        valid_until=parsed["valid_until"],
    )
    result = evaluate_vc(facts, on=now)
    kinds = []
    if result.integrity:
        kinds.append(EvidenceKind.SIGNATURE_VALID)
    if result.tier >= EvidenceTier.ISSUER_AUTHENTICATED:
        kinds.append(EvidenceKind.SIGNATURE_FROM_REGISTERED_ISSUER)
    explanation = explain(
        ClaimType.ACADEMIC_AWARD,
        result.tier,
        kinds or [EvidenceKind.HOLDER_STATEMENT],
        decision="ACCEPTED_AS_CLAIM" if result.accepted_as_claim else "NOT_ACCEPTED",
        checked_at=now,
    )
    return VcOutcome(kind, result, explanation, issuer)


def _holder_bound(
    vc: Mapping[str, Any],
    vc_jwt: str,
    holder_proof: str,
    settings: verifier.VerifierSettings,
    now: datetime,
) -> bool:
    """The holder-signed vp+jwt must verify under the key the credential is bound to, envelop THIS credential, name this verifier
    as audience and be fresh. Anything else is not binding."""
    jwk = (vc.get("cnf") or {}).get("jwk")
    if not isinstance(jwk, dict):
        return False
    try:
        _, vp = verify_external_jws(
            holder_proof, keys=[jwk], algorithms=["ES256", "Ed25519"], typ=vcdm.VP_TYP
        )
        iat = vp.get("iat")
        return (
            vp.get("aud") == settings.client_id
            and isinstance(iat, int | float)
            and abs(now.timestamp() - iat) <= verifier.KB_MAX_AGE.total_seconds()
            and vcdm.enveloped_credential(vp) == vc_jwt
        )
    except (CryptoError, CredentialError):
        return False
