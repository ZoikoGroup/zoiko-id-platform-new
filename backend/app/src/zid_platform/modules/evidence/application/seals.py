"""Check a signed or sealed XML document and say what was and was not established (GID-051; ZID-INT-002 section 09).

One entry point for other modules (through `evidence.ports`): the signature result and the seal (qualified status) result of the same
document, each with its own status. It adds no cryptography: it runs the XAdES validator and the existing trusted-list logic.

A valid seal says WHO sealed the bytes and that they are unchanged. It does not say the sealer was the awarding body it names, that the
award is true, or that the credential is a licence: those are separate questions with separate sources.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.serialization import Encoding

from zid_platform.modules.evidence.application import cms, signatures, trust, xades
from zid_platform.modules.evidence.domain import documents as docs

_ORGANIZATION_IDENTIFIER = x509.ObjectIdentifier("2.5.4.97")


def _identity(cert: x509.Certificate | None) -> tuple[str | None, str | None]:
    """(hex SHA-256 of the certificate, its organizationIdentifier if it carries one): what a registry pins an issuer's seals by."""
    if cert is None:
        return None, None
    digest = hashes.Hash(hashes.SHA256())
    digest.update(cert.public_bytes(Encoding.DER))
    org = cert.subject.get_attributes_for_oid(_ORGANIZATION_IDENTIFIER)
    return digest.finalize().hex(), (str(org[0].value) if org else None)


@dataclass(frozen=True, slots=True)
class SealCheck:
    signature: docs.CheckResult
    seal: docs.CheckResult
    signer: str | None
    signer_sha256: str | None  # of the certificate that actually sealed, for pinning by a registry
    signer_organization_identifier: str | None
    sealed: (
        bool  # the cryptography is sound AND the chain ends at an anchor this deployment configured
    )


def check_signed_xml(
    xml: bytes, config: trust.TrustConfig | None = None, *, at: datetime | None = None
) -> SealCheck:
    if config is None:
        from zid_platform.modules.evidence.infrastructure.trust_config import trust_config

        config = trust_config()
    when = at or datetime.now(UTC)
    findings = xades.inspect_xml(
        xml,
        anchors=config.anchors,
        at=when,
        tsa_roots=config.tsa_roots,
        crls=config.crls,
        ocsp_responses=config.ocsp_responses,
    )
    if not findings:
        none = docs.CheckResult("signature", docs.Status.INDETERMINATE, ("NO_SIGNATURE_FOUND",), {})
        return SealCheck(
            none,
            docs.CheckResult(
                "seal",
                docs.Status.UNAVAILABLE,
                ("NO_TRUSTED_SIGNATURE_TO_ASSESS",),
                {"qualified_status": "NOT_ASSESSED"},
            ),
            None,
            None,
            None,
            False,
        )
    sig = signatures.signature_result(findings, config)
    seal = signatures.seal_result(findings, config, when)
    sealed = sig.status in {docs.Status.VERIFIED, docs.Status.VERIFIED_LIMITED}
    # the certificate that is reported is the first signature that is both sound and chained to a configured anchor, else the first
    chosen = next(
        (f for f in findings if f.cryptographically_valid and f.chain is cms.Chain.TRUSTED),
        findings[0],
    )
    leaf = chosen.chain_certificates[0] if chosen.chain_certificates else None
    sha, org = _identity(leaf)
    return SealCheck(sig, seal, chosen.signer, sha, org, sealed)
