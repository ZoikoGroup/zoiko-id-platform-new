"""Signing a private-key client assertion (RFC 7523) and reading a public key. Only ES256 (EC P-256) and EdDSA (Ed25519), the two the Verify API accepts."""

from __future__ import annotations

import base64
import json
from typing import Any

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature, encode_dss_signature


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def b64url_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def load_private_key(
    pem: str | bytes, password: bytes | None = None
) -> ec.EllipticCurvePrivateKey | ed25519.Ed25519PrivateKey:
    key = serialization.load_pem_private_key(pem.encode() if isinstance(pem, str) else pem, password=password)
    if isinstance(key, ed25519.Ed25519PrivateKey):
        return key
    if isinstance(key, ec.EllipticCurvePrivateKey) and isinstance(key.curve, ec.SECP256R1):
        return key
    raise ValueError("the private key must be EC P-256 or Ed25519")


def algorithm_of(key: ec.EllipticCurvePrivateKey | ed25519.Ed25519PrivateKey) -> str:
    return "EdDSA" if isinstance(key, ed25519.Ed25519PrivateKey) else "ES256"


def sign_jws(
    key: ec.EllipticCurvePrivateKey | ed25519.Ed25519PrivateKey, payload: dict[str, Any], *, key_id: str | None
) -> str:
    """A compact JWS. The header carries the algorithm, `typ` JWT and the key id when given."""
    header: dict[str, Any] = {"alg": algorithm_of(key), "typ": "JWT"}
    if key_id:
        header["kid"] = key_id
    signing_input = (
        b64url(json.dumps(header, separators=(",", ":")).encode())
        + "."
        + b64url(json.dumps(payload, separators=(",", ":")).encode())
    )
    if isinstance(key, ed25519.Ed25519PrivateKey):
        signature = key.sign(signing_input.encode())
    else:
        r, s = decode_dss_signature(key.sign(signing_input.encode(), ec.ECDSA(hashes.SHA256())))
        signature = r.to_bytes(32, "big") + s.to_bytes(32, "big")  # JWS wants r || s, not DER
    return signing_input + "." + b64url(signature)


def verify_signature(jwk: dict[str, Any], data: bytes, signature: bytes) -> None:
    """Check a signature with a public JWK (EC P-256 as r || s, or Ed25519). Raises when it is wrong."""
    if jwk.get("kty") == "EC" and jwk.get("crv") == "P-256":
        if len(signature) != 64:
            raise ValueError("bad signature length")
        numbers = ec.EllipticCurvePublicNumbers(
            int.from_bytes(b64url_decode(jwk["x"]), "big"),
            int.from_bytes(b64url_decode(jwk["y"]), "big"),
            ec.SECP256R1(),
        )
        numbers.public_key().verify(
            encode_dss_signature(int.from_bytes(signature[:32], "big"), int.from_bytes(signature[32:], "big")),
            data,
            ec.ECDSA(hashes.SHA256()),
        )
    elif jwk.get("kty") == "OKP" and jwk.get("crv") == "Ed25519":
        ed25519.Ed25519PublicKey.from_public_bytes(b64url_decode(jwk["x"])).verify(signature, data)
    else:
        raise ValueError("only EC P-256 and Ed25519 public keys")
