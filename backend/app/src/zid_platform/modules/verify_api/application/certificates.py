"""Reading a client certificate a partner registers for sign-in (CR-165). Application layer, not domain: it parses X.509, which the pure domain does not import."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from zid_platform.modules.verify_api.domain.credential_options import CLOCK_SKEW
from zid_platform.modules.verify_api.domain.model import PartnerError


@dataclass(frozen=True, slots=True)
class CertFacts:
    """What is kept of a registered certificate: its fingerprint (the pin), who it names (for a person to recognise it), and when it ends."""

    sha256: bytes
    subject: str
    not_after: datetime


def check_certificate(pem: Any, *, now: datetime) -> CertFacts:
    """A single, current, public certificate in PEM. Refused: anything with private key material, more than one certificate (a chain), one that is not yet valid or has ended, and a key too weak to rely on."""
    from cryptography import x509
    from cryptography.hazmat.primitives.asymmetric import ec, ed25519, rsa
    from cryptography.hazmat.primitives.serialization import Encoding

    if not isinstance(pem, str) or not 100 <= len(pem) <= 8192:
        raise PartnerError("VALIDATION_FAILED", "Send one certificate in PEM form.")
    if "PRIVATE KEY" in pem.upper():
        raise PartnerError(
            "VALIDATION_FAILED",
            "That contains private key material. Send only the certificate, and treat that private key as exposed.",
        )
    if pem.count("BEGIN CERTIFICATE") != 1:
        raise PartnerError(
            "VALIDATION_FAILED",
            "Send exactly one certificate, the one your server presents. A chain is not accepted: the certificate itself is pinned.",
        )
    try:
        cert = x509.load_pem_x509_certificate(pem.encode())
    except ValueError as e:
        raise PartnerError("VALIDATION_FAILED", "That is not a valid PEM certificate.") from e
    if cert.not_valid_before_utc > now + CLOCK_SKEW:
        raise PartnerError("VALIDATION_FAILED", "That certificate is not valid yet.")
    if cert.not_valid_after_utc <= now:
        raise PartnerError("VALIDATION_FAILED", "That certificate has expired.")
    key = cert.public_key()
    weak = (isinstance(key, rsa.RSAPublicKey) and key.key_size < 2048) or not isinstance(
        key, rsa.RSAPublicKey | ec.EllipticCurvePublicKey | ed25519.Ed25519PublicKey
    )
    if weak or (isinstance(key, ec.EllipticCurvePublicKey) and key.curve.key_size < 256):
        raise PartnerError(
            "VALIDATION_FAILED",
            "The certificate's key is too weak: use RSA 2048 or more, an EC P-256 or stronger key, or Ed25519.",
        )
    return CertFacts(
        hashlib.sha256(cert.public_bytes(Encoding.DER)).digest(),
        cert.subject.rfc4514_string()[:200],
        cert.not_valid_after_utc,
    )
