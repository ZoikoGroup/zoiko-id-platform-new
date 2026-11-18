"""The production signature check for registry extracts (WF02 K03): Ed25519, ECDSA P-256 with SHA-256 and RSA-PSS with SHA-256, nothing else. The rules that decide whether a signature may count are
`domain/extract_authenticity.py`; this only answers "does this key verify this signature over this payload"."""

from __future__ import annotations

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, padding, rsa

from zid_platform.modules.organization.domain.extract_authenticity import TrustedKey


def verify_with_cryptography(key: TrustedKey, payload: bytes, signature: bytes) -> bool:
    try:
        public = serialization.load_pem_public_key(key.public_key_pem)
    except ValueError:
        return False
    try:
        if key.algorithm == "Ed25519" and isinstance(public, ed25519.Ed25519PublicKey):
            public.verify(signature, payload)
        elif (
            key.algorithm == "ES256"
            and isinstance(public, ec.EllipticCurvePublicKey)
            and public.curve.name == "secp256r1"
        ):
            public.verify(signature, payload, ec.ECDSA(hashes.SHA256()))
        elif (
            key.algorithm == "PS256"
            and isinstance(public, rsa.RSAPublicKey)
            and public.key_size >= 2048
        ):
            public.verify(
                signature,
                payload,
                padding.PSS(padding.MGF1(hashes.SHA256()), padding.PSS.DIGEST_LENGTH),
                hashes.SHA256(),
            )
        else:
            return False
    except (InvalidSignature, ValueError):
        return False
    return True
