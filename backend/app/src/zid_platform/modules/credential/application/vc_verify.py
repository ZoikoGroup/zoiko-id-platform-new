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
An EDC (European Digital Credential) delivered as a vc+jwt is verified like any other VC, with its JWS as the seal under the issuer's pinned key and qualified status
NOT_ASSESSED. XML EDC e-seals (XAdES) are validated on `/v2/credentials/edc/verify`. A JSON-LD credential secured by a Data Integrity proof needs RDF
canonicalization, which is not here, so it never reaches this function (it is not a vc+jwt) and is refused as malformed, not guessed.
"""

from __future__ import annotations

import base64
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection
from zid_crypto import CryptoError, verify_external_jws

from zid_platform.modules.credential.application import data_integrity, verifier
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
    # read only from a credential whose proof verified; never part of the contract the generic verify routes return
    subject_claims: Mapping[str, Any] | None = None
    credential_types: tuple[str, ...] = ()
    issuer_trusted: bool = (
        False  # the typed trust table vouches for this issuer for this credential type
    )
    issuer_known: bool = False  # the issuer is in the typed trust table at all; if not, the signature could not even be tried
    issued_at: datetime | None = (
        None  # the credential's own issue date (validFrom), used to judge a register listing on the day it was issued
    )

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
            **({"qualified_seal": "NOT_ASSESSED"} if self.kind is Kind.EDC else {}),
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
    bound = bool(
        proof_valid and holder_proof and _holder_bound(vc, vc_jwt, holder_proof, settings, now)
    )
    return _conclude(
        kind,
        issuer,
        vc if proof_valid else vc_unverified,
        proof_valid=proof_valid,
        in_registry=in_registry,
        status=status,
        bound=bound,
        now=now,
        issuer_known=found is not None,
    )


def _conclude(
    kind: Kind,
    issuer: str | None,
    doc: Mapping[str, Any],
    *,
    proof_valid: bool,
    in_registry: bool,
    status: CredentialStatus,
    bound: bool,
    now: datetime,
    issuer_known: bool = False,
) -> VcOutcome:
    """The answer from the facts, the same for a vc+jwt and a Data Integrity credential: five separate facts, never a primary-source
    confirmation or a license."""
    parsed = facts_from_json(doc, kind)
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
        # An EDC secured by a JWS or a Data Integrity proof is sealed by that proof: the signature under the issuer's own pinned key. It is
        # NOT a qualified e-seal (that is the XML path, /v2/credentials/edc/verify), so the response says qualified status is not assessed.
        seal_valid=kind is Kind.EDC and proof_valid,
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
    subject = doc.get("credentialSubject")
    if isinstance(subject, list):
        subject = subject[0] if subject else None
    return VcOutcome(
        kind,
        result,
        explanation,
        issuer,
        subject if proof_valid and isinstance(subject, Mapping) else None,
        tuple(t for t in vcdm._types(dict(doc)) if t != "VerifiableCredential"),
        proof_valid and in_registry,
        issuer_known,
        parsed["valid_from"],
    )


def _proof_key(
    keys: Mapping[str, Any], verification_method: str, suite: str
) -> data_integrity.VerificationKey | None:
    """The issuer's PINNED key that the proof names, of the type its suite uses (Ed25519 for eddsa-rdfc-2022, P-256 or P-384 for ecdsa-rdfc-2019), or None.
    A key matches when its `kid` is the verification method or its fragment; a key of another type is never used."""
    fragment = verification_method.rsplit("#", 1)[-1]
    wants = {
        "eddsa-rdfc-2022": (("OKP", "Ed25519"),),
        "eddsa-jcs-2022": (("OKP", "Ed25519"),),
        "ecdsa-rdfc-2019": (("EC", "P-256"), ("EC", "P-384")),
        "ecdsa-jcs-2019": (("EC", "P-256"), ("EC", "P-384")),
    }.get(suite)
    if wants is None:
        return None

    def b64(value: str) -> bytes | None:
        try:
            return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        except ValueError:
            return None

    for jwk in keys.get("keys", []):
        if (jwk.get("kty"), jwk.get("crv")) not in wants or not jwk.get("x"):
            continue
        kid = str(jwk.get("kid", ""))
        if not kid or kid not in {verification_method, fragment}:
            continue
        x = b64(jwk["x"])
        if jwk.get("kty") == "OKP":
            return data_integrity.VerificationKey("ed25519", x) if x and len(x) == 32 else None
        y = b64(str(jwk.get("y", "")))
        size, kind = (32, "p256") if jwk.get("crv") == "P-256" else (48, "p384")
        if x and y and len(x) == size and len(y) == size:
            return data_integrity.VerificationKey(
                kind, b"\x04" + x + y
            )  # an uncompressed SEC1 point
        return None
    return None


async def verify_vc_di(
    conn: AsyncConnection,
    settings: verifier.VerifierSettings,
    contexts: data_integrity.BundledContexts,
    *,
    vc: Mapping[str, Any],
    jurisdiction: str | None,
    now: datetime,
    holder_presentation: Mapping[str, Any] | None = None,
) -> VcOutcome:
    """A JSON-LD credential secured by a Data Integrity proof (eddsa-rdfc-2022 or ecdsa-rdfc-2019), verified offline against the issuer's PINNED key.

    The issuer must be in the typed trust table for this credential type, and the proof must name a key that table holds: a good signature from
    a key nobody vouched for is integrity at most. Holder binding needs a holder-signed presentation (`holder_presentation`, see
    `holder_bound_di`); without a bound holder such a credential is never accepted as a claim on its own. Contexts come only from the
    hash-pinned bundle; nothing is fetched."""
    doc = dict(vc)
    try:
        issuer = vcdm.issuer_of(doc)
    except CredentialError as e:
        raise CredentialError("VC_MALFORMED", e.code) from e
    kind = kind_of(doc)
    ctype = next((t for t in reversed(vcdm._types(doc)) if t != "VerifiableCredential"), "")
    proof = doc.get("proof")
    vm = str(proof.get("verificationMethod", "")) if isinstance(proof, Mapping) else ""
    proof_valid, in_registry, status = False, False, CredentialStatus.UNKNOWN
    reasons: tuple[str, ...] = ()
    found = await store.load_trust(conn, issuer)
    if found is not None and vm:
        trust, row = found
        try:
            keys = await verifier._issuer_keys(conn, settings, row, now)
        except CredentialError:
            keys = {"keys": []}
        key = _proof_key(keys, vm, str((proof or {}).get("cryptosuite", "")))
        if key is not None:
            verdict = data_integrity.verify(doc, key, contexts)
            proof_valid, reasons = verdict.status == "VERIFIED", verdict.reasons
        if proof_valid:
            try:
                trust_decision(trust, credential_type=ctype, jurisdiction=jurisdiction, now=now)
                in_registry = True
            except CredentialError:
                in_registry = False  # a good signature from an issuer not trusted for this type: integrity only
            if "credentialStatus" in doc:
                status = STATUS_MAP.get(
                    await verifier._bitstring_status(
                        settings, doc, issuer, keys, list(row["algorithms"]), now
                    ),
                    CredentialStatus.UNKNOWN,
                )
    bound = bool(
        proof_valid
        and holder_presentation is not None
        and holder_bound_di(doc, holder_presentation, settings.client_id, contexts, now)
    )
    out = _conclude(
        kind,
        issuer,
        doc,
        proof_valid=proof_valid,
        in_registry=in_registry,
        status=status,
        bound=bound,
        now=now,
        issuer_known=found is not None,
    )
    if not proof_valid and reasons:  # say WHY the proof was not accepted, by its own name
        merged = tuple(dict.fromkeys((*out.result.reasons, *reasons)))
        out = replace(out, result=replace(out.result, reasons=merged))
    return out


def holder_bound_di(
    vc: Mapping[str, Any],
    presentation: Mapping[str, Any],
    audience: str,
    contexts: data_integrity.BundledContexts,
    now: datetime,
) -> bool:
    """Is the person presenting this JSON-LD credential the one it was issued to? True only when ALL of these hold; anything else is not binding.

    - the holder is a `did:key` (self-certifying, so nothing is fetched and nobody vouches for the key: any other DID method is not binding here);
    - the presentation is a VerifiablePresentation naming that holder, carrying exactly THIS credential (a copy of someone else's is not bound);
    - the credential's subject id IS that DID: the credential was issued to the key that is now signing;
    - the presentation's Data Integrity proof verifies under that DID's key with proofPurpose `authentication`, a `domain` equal to this verifier
      (a presentation made for another verifier is not reusable here) and a `challenge`, and its `created` is within the same window as the JWT form."""
    holder = presentation.get("holder")
    if isinstance(holder, Mapping):
        holder = holder.get("id")
    if not isinstance(holder, str) or not holder.startswith("did:key:"):
        return False
    if "VerifiablePresentation" not in vcdm._types(dict(presentation)):
        return False
    carried = presentation.get("verifiableCredential")
    if isinstance(carried, list):
        carried = carried[0] if len(carried) == 1 else None
    if carried is None or _same_json(carried, vc) is False:
        return False
    subject = vc.get("credentialSubject")
    subject_id = subject.get("id") if isinstance(subject, Mapping) else None
    if subject_id != holder:
        return False
    proof = presentation.get("proof")
    if not isinstance(proof, Mapping):
        return False
    if proof.get("domain") != audience or not str(proof.get("challenge") or "").strip():
        return False
    try:
        created = datetime.fromisoformat(str(proof.get("created")))
        if (
            created.tzinfo is None
            or abs(now.timestamp() - created.timestamp()) > verifier.KB_MAX_AGE.total_seconds()
        ):
            return False
        vm = str(proof.get("verificationMethod", ""))
        if vm.partition("#")[0] != holder:
            return False
        key = data_integrity.did_key_verification_key(vm)
    except (ValueError, data_integrity.DataIntegrityError):
        return False
    verdict = data_integrity.verify(presentation, key, contexts, purpose="authentication")
    return verdict.status == "VERIFIED"


def _same_json(a: Any, b: Any) -> bool:
    import json

    return json.dumps(a, sort_keys=True, separators=(",", ":")) == json.dumps(
        b, sort_keys=True, separators=(",", ":")
    )


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
