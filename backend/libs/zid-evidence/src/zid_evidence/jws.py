"""Offline JWS verification for evidence signatures (DEC-F2 §1, §6).

A deliberately small verifier so that an external party can check checkpoint and record
signatures with only this package and `cryptography`: compact JWS, ES256 (P-256, raw R||S) or
Ed25519, public keys from a JWK set chosen by `kid`. Remote key references (`jku`, `x5u`, `x5c`,
embedded `jwk`) are never followed, and a key's own `alg`, when present, must match the header.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Mapping, Sequence
from typing import Any

__all__ = ["ALLOWED_ALGS", "SignatureError", "verify_compact"]

ALLOWED_ALGS = ("ES256", "Ed25519", "EdDSA")


class SignatureError(ValueError):
    code = "EVIDENCE_SIGNATURE_FAILED"


def _b64d(text: str) -> bytes:
    try:
        return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except ValueError as e:
        raise SignatureError("malformed base64url") from e


def _verify(jwk: Mapping[str, Any], alg: str, signing_input: bytes, sig: bytes) -> bool:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec, ed25519
    from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

    if any(k in jwk for k in ("d", "p", "q", "k")):
        raise SignatureError("a verification key must not carry private members")
    try:
        if alg == "ES256":
            if jwk.get("kty") != "EC" or jwk.get("crv") != "P-256" or len(sig) != 64:
                return False
            x = int.from_bytes(_b64d(str(jwk["x"])), "big")
            y = int.from_bytes(_b64d(str(jwk["y"])), "big")
            pub = ec.EllipticCurvePublicNumbers(x, y, ec.SECP256R1()).public_key()
            der = encode_dss_signature(
                int.from_bytes(sig[:32], "big"), int.from_bytes(sig[32:], "big")
            )
            pub.verify(der, signing_input, ec.ECDSA(hashes.SHA256()))
            return True
        if jwk.get("kty") != "OKP" or jwk.get("crv") != "Ed25519":
            return False
        ed25519.Ed25519PublicKey.from_public_bytes(_b64d(str(jwk["x"]))).verify(
            sig, signing_input
        )
        return True
    except (InvalidSignature, ValueError, KeyError):
        return False


def verify_compact(
    token: str, *, keys: Sequence[Mapping[str, Any]], typ: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Verify a compact JWS; return (header, payload). Raises SignatureError."""
    parts = token.split(".") if isinstance(token, str) else []
    if len(parts) != 3:
        raise SignatureError("malformed compact JWS")
    try:
        header = json.loads(_b64d(parts[0]))
        payload = json.loads(_b64d(parts[1]))
    except ValueError as e:
        raise SignatureError("malformed JWS header or payload") from e
    if not isinstance(header, dict) or not isinstance(payload, dict):
        raise SignatureError("JWS header and payload must be JSON objects")
    alg = header.get("alg")
    if alg not in ALLOWED_ALGS:
        raise SignatureError(f"algorithm not allowed: {alg!r}")
    if header.get("typ") != typ:
        raise SignatureError(f"expected typ {typ}")
    if {"jku", "x5u", "x5c", "jwk"} & header.keys():
        raise SignatureError("remote or embedded keys are never trusted")
    kid = header.get("kid")
    candidates = [k for k in keys if k.get("kid") == kid]
    if len(candidates) != 1:
        raise SignatureError(f"unknown signing key {kid!r}")
    jwk = candidates[0]
    if jwk.get("alg") not in (None, alg):
        raise SignatureError("key algorithm does not match the header")
    signing_input = f"{parts[0]}.{parts[1]}".encode("ascii")
    if not _verify(jwk, str(alg), signing_input, _b64d(parts[2])):
        raise SignatureError("signature does not verify")
    return header, payload
