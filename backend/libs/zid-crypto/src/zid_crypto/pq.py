"""ML-DSA signatures in JOSE and classical+PQ dual signing (DEC-F6 PQC-2; ZID-CRYPTO-001 §12, §13).

- `MlDsaSigner` is a `zid_crypto.Signer` for ML-DSA-65 (FIPS 204). Its public key is the JOSE
  "AKP" key type of RFC 9964 (`{"kty":"AKP","alg":"ML-DSA-65","pub":<b64url>}`); the JWS signature is
  the raw ML-DSA signature over the JWS signing input with the empty context string.
- `verify_mldsa_jws` verifies a compact JWS the same strict way `verify_jws` does for classical
  algorithms (explicit `typ`, no remote key references, header `alg` must equal the key's `alg`).
- `sign_dual_jws` / `verify_dual_jws` carry one payload under several signatures (RFC 7515 §7.2
  general JSON serialization): the classical signature stays verifiable by a verifier that only
  knows ES256/Ed25519 (it skips algorithms it does not support), and a full verifier requires every
  algorithm its profile names. A signature a verifier does support but that fails is always fatal.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric import mldsa

from zid_crypto import (
    ALLOWED_JWS_ALGS,
    CryptoError,
    Signer,
    b64url,
    b64url_decode,
    peek_header,
    verify_jws,
)
from zid_crypto.purposes import KeyPurpose

MLDSA_ALG = "ML-DSA-65"
MLDSA_PUBLIC_BYTES = 1952
PQ_JWS_ALGS: tuple[str, ...] = (MLDSA_ALG,)


class MlDsaSigner:
    """In-process ML-DSA-65 key (development/tests); deployed cells use a validated KMS/HSM."""

    def __init__(self, kid: str, *, purpose: KeyPurpose | None = None) -> None:
        if purpose is not None:
            from zid_crypto.policy import default_registry

            default_registry().require(purpose, MLDSA_ALG)
        self._kid = kid
        self._key = mldsa.MLDSA65PrivateKey.generate()

    @property
    def kid(self) -> str:
        return self._kid

    @property
    def alg(self) -> str:
        return MLDSA_ALG

    def sign(self, signing_input: bytes) -> bytes:
        return self._key.sign(signing_input)

    def public_jwk(self) -> dict[str, Any]:
        raw = self._key.public_key().public_bytes_raw()
        return {"kty": "AKP", "alg": MLDSA_ALG, "pub": b64url(raw), "kid": self._kid, "use": "sig"}


def _mldsa_public(jwk: Mapping[str, Any]) -> mldsa.MLDSA65PublicKey:
    if any(k in jwk for k in ("priv", "d")):
        raise CryptoError("JWK_PRIVATE_MATERIAL", "a public key must not carry private members")
    if jwk.get("kty") != "AKP" or jwk.get("alg") != MLDSA_ALG:
        raise CryptoError("JWK_UNSUPPORTED", "only AKP ML-DSA-65 public keys")
    pub = jwk.get("pub")
    try:
        raw = b64url_decode(pub) if isinstance(pub, str) else b""
        if len(raw) != MLDSA_PUBLIC_BYTES:
            raise ValueError("length")
        return mldsa.MLDSA65PublicKey.from_public_bytes(raw)
    except ValueError as e:
        raise CryptoError("JWK_UNSUPPORTED", "malformed ML-DSA public key") from e


def _verify_signature(
    protected_b64: str,
    payload_b64: str,
    signature_b64: str,
    *,
    keys: Sequence[Mapping[str, Any]],
    typ: str,
) -> dict[str, Any]:
    """Verify one ML-DSA signature over `protected.payload`; returns the decoded payload."""
    try:
        header = json.loads(b64url_decode(protected_b64))
    except ValueError as e:
        raise CryptoError("JWS_MALFORMED") from e
    if not isinstance(header, dict):
        raise CryptoError("JWS_MALFORMED")
    if header.get("alg") not in PQ_JWS_ALGS:
        raise CryptoError("ALG_NOT_ALLOWED", str(header.get("alg")))
    if header.get("typ") != typ:
        raise CryptoError("JWS_TYP_INVALID", f"expected {typ}")
    if any(h in header for h in ("jku", "x5u", "x5c", "jwk")):
        raise CryptoError("JWS_HEADER_REJECTED", "remote key references are never followed")
    candidates = [k for k in keys if k.get("kid") == header.get("kid")]
    if len(candidates) != 1:
        raise CryptoError("KEY_UNKNOWN", str(header.get("kid")))
    public = _mldsa_public(candidates[0])
    try:
        public.verify(
            b64url_decode(signature_b64), f"{protected_b64}.{payload_b64}".encode("ascii")
        )
        payload = json.loads(b64url_decode(payload_b64))
    except (InvalidSignature, ValueError) as e:
        raise CryptoError("SIGNATURE_INVALID") from e
    if not isinstance(payload, dict):
        raise CryptoError("JWS_MALFORMED", "payload must be a JSON object")
    return payload


def verify_mldsa_jws(
    token: str, *, keys: Sequence[Mapping[str, Any]], typ: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Verify a compact JWS signed with ML-DSA-65. Returns (header, payload)."""
    if token.count(".") != 2:
        raise CryptoError("JWS_MALFORMED")
    protected, payload_b64, signature = token.split(".")
    payload = _verify_signature(protected, payload_b64, signature, keys=keys, typ=typ)
    return peek_header(token), payload


def sign_dual_jws(
    signers: Sequence[Signer], payload: Mapping[str, Any], *, typ: str
) -> dict[str, Any]:
    """One payload, one signature per signer (general JSON serialization)."""
    if len({s.alg for s in signers}) != len(signers) or not signers:
        raise CryptoError("JWS_MALFORMED", "one signature per distinct algorithm")
    payload_b64 = b64url(json.dumps(payload, separators=(",", ":"), default=str).encode())
    signatures = []
    for signer in signers:
        header = {"alg": signer.alg, "kid": signer.kid, "typ": typ}
        protected = b64url(json.dumps(header, separators=(",", ":")).encode())
        signing_input = f"{protected}.{payload_b64}".encode("ascii")
        signatures.append({"protected": protected, "signature": b64url(signer.sign(signing_input))})
    return {"payload": payload_b64, "signatures": signatures}


def verify_dual_jws(
    document: Mapping[str, Any],
    *,
    keys: Sequence[Mapping[str, Any]],
    typ: str,
    supported: Sequence[str],
    required: Sequence[str],
) -> tuple[dict[str, Any], tuple[str, ...]]:
    """Verify a multi-signature document the way one verifier profile does.

    `supported`: algorithms this verifier implements (a classical-only verifier passes
    `("ES256",)`); signatures under any other algorithm are skipped, never trusted. `required`:
    algorithms that must have verified (a subset of `supported`). A supported signature that fails
    raises, so tampering cannot hide behind a signature the verifier ignores. Returns
    (payload, algorithms verified)."""
    if not set(required) <= set(supported):
        raise CryptoError("ALG_NOT_ALLOWED", "required algorithms must be supported")
    if not set(supported) <= {*ALLOWED_JWS_ALGS, *PQ_JWS_ALGS}:
        raise CryptoError("ALG_NOT_ALLOWED", "unknown algorithm in the verifier profile")
    payload_b64, signatures = document.get("payload"), document.get("signatures")
    if not isinstance(payload_b64, str) or not isinstance(signatures, list) or not signatures:
        raise CryptoError("JWS_MALFORMED")
    verified: dict[str, dict[str, Any]] = {}
    for entry in signatures:
        protected, signature = entry.get("protected"), entry.get("signature")
        if not isinstance(protected, str) or not isinstance(signature, str):
            raise CryptoError("JWS_MALFORMED")
        alg = peek_header(protected + ".").get("alg")
        if alg not in supported:
            continue
        if alg in PQ_JWS_ALGS:
            body = _verify_signature(protected, payload_b64, signature, keys=keys, typ=typ)
        else:
            _, body = verify_jws(
                f"{protected}.{payload_b64}.{signature}", keys=keys, typ=typ, algorithms=(alg,)
            )
        verified[alg] = body
    missing = [a for a in required if a not in verified]
    if missing or not verified:
        raise CryptoError("SIGNATURE_INVALID", f"no verified signature for {missing or 'any'}")
    return next(iter(verified.values())), tuple(sorted(verified))


__all__ = [
    "MLDSA_ALG",
    "MlDsaSigner",
    "sign_dual_jws",
    "verify_dual_jws",
    "verify_mldsa_jws",
]
