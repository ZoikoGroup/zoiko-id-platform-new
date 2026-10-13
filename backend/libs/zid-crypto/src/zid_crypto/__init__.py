"""Signing keys, JWS and JWKS for Zoiko iD (DEC-F5; ZID-AUTH-001 §17; DEC-E1 §2).

- Private keys live only behind the `Signer` protocol. `LocalSigner` is for development and tests;
  production signers delegate to KMS/HSM and never expose key material (no library holds it).
- One algorithm allowlist (`ALLOWED_JWS_ALGS`) for everything we sign or accept: `none`, HMAC and
  RSA-PKCS1 are never accepted, so algorithm-confusion attacks have nothing to confuse.
- `KeyRing` signs with exactly one active key and publishes the active key plus keys still inside
  their verification window, so rotation never breaks tokens already issued.
"""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from cryptography.hazmat.primitives.asymmetric import ec, ed25519
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from joserfc import jws
from joserfc.errors import JoseError
from joserfc.jwk import ECKey, OKPKey, RSAKey

ALLOWED_JWS_ALGS: tuple[str, ...] = ("ES256", "Ed25519")  # fully-specified algs (RFC 9864)
# Verification only, for tokens *other* parties sign (enterprise IdP ID tokens, SSF transmitters,
# trust-list signers). Zoiko never signs with these; each integration narrows the list further.
EXTERNAL_VERIFY_ALGS: tuple[str, ...] = ("ES256", "Ed25519", "RS256", "PS256")
MIN_RSA_BITS = 2048


class CryptoError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64url_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def jwk_thumbprint(jwk: Mapping[str, Any]) -> str:
    """RFC 7638 SHA-256 thumbprint over the required public members only."""
    required = {"EC": ("crv", "kty", "x", "y"), "OKP": ("crv", "kty", "x")}.get(str(jwk.get("kty")))
    if required is None or any(k not in jwk for k in required):
        raise CryptoError("JWK_UNSUPPORTED", "only EC P-256 and OKP Ed25519 public keys")
    canonical = json.dumps({k: jwk[k] for k in required}, separators=(",", ":"), sort_keys=True)
    return b64url(hashlib.sha256(canonical.encode()).digest())


class Signer(Protocol):
    @property
    def kid(self) -> str: ...
    @property
    def alg(self) -> str: ...
    def sign(self, signing_input: bytes) -> bytes: ...
    def public_jwk(self) -> dict[str, Any]: ...


class LocalSigner:
    """In-process key for development and tests only (production uses a KMS/HSM-backed Signer)."""

    def __init__(self, kid: str, alg: str = "ES256") -> None:
        if alg not in ALLOWED_JWS_ALGS:
            raise CryptoError("ALG_NOT_ALLOWED", alg)
        self._kid, self._alg = kid, alg
        self._key: ec.EllipticCurvePrivateKey | ed25519.Ed25519PrivateKey = (
            ec.generate_private_key(ec.SECP256R1())
            if alg == "ES256"
            else ed25519.Ed25519PrivateKey.generate()
        )

    @property
    def kid(self) -> str:
        return self._kid

    @property
    def alg(self) -> str:
        return self._alg

    def sign(self, signing_input: bytes) -> bytes:
        if isinstance(self._key, ec.EllipticCurvePrivateKey):
            from cryptography.hazmat.primitives import hashes

            r, s = decode_dss_signature(self._key.sign(signing_input, ec.ECDSA(hashes.SHA256())))
            return r.to_bytes(32, "big") + s.to_bytes(32, "big")  # JWS uses raw R||S, not DER
        return self._key.sign(signing_input)

    def public_jwk(self) -> dict[str, Any]:
        pub = self._key.public_key()
        if isinstance(pub, ec.EllipticCurvePublicKey):
            n = pub.public_numbers()
            jwk = {
                "kty": "EC",
                "crv": "P-256",
                "x": b64url(n.x.to_bytes(32, "big")),
                "y": b64url(n.y.to_bytes(32, "big")),
            }
        else:
            from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

            jwk = {
                "kty": "OKP",
                "crv": "Ed25519",
                "x": b64url(pub.public_bytes(Encoding.Raw, PublicFormat.Raw)),
            }
        return {**jwk, "kid": self._kid, "alg": self._alg, "use": "sig"}


def sign_jws(
    signer: Signer,
    payload: Mapping[str, Any],
    *,
    typ: str,
    extra_header: Mapping[str, Any] | None = None,
) -> str:
    """Compact JWS with `alg`, `kid` and an explicit `typ` (RFC 8725 §3.11: explicit typing)."""
    header = {**(extra_header or {}), "alg": signer.alg, "kid": signer.kid, "typ": typ}
    signing_input = (
        b64url(json.dumps(header, separators=(",", ":")).encode())
        + "."
        + b64url(json.dumps(payload, separators=(",", ":"), default=str).encode())
    )
    return signing_input + "." + b64url(signer.sign(signing_input.encode("ascii")))


def _import(jwk: Mapping[str, Any]) -> ECKey | OKPKey:
    if any(k in jwk for k in ("d", "p", "q", "k")):
        raise CryptoError("JWK_PRIVATE_MATERIAL", "a public key must not carry private members")
    if jwk.get("kty") == "EC" and jwk.get("crv") == "P-256":
        return ECKey.import_key(dict(jwk))
    if jwk.get("kty") == "OKP" and jwk.get("crv") == "Ed25519":
        return OKPKey.import_key(dict(jwk))
    raise CryptoError("JWK_UNSUPPORTED", "only EC P-256 and OKP Ed25519 public keys")


def peek_header(token: str) -> dict[str, Any]:
    try:
        header = json.loads(b64url_decode(token.split(".")[0]))
    except (ValueError, IndexError) as e:
        raise CryptoError("JWS_MALFORMED") from e
    if not isinstance(header, dict):
        raise CryptoError("JWS_MALFORMED")
    return header


def verify_jws(
    token: str,
    *,
    keys: Sequence[Mapping[str, Any]],
    typ: str,
    algorithms: Sequence[str] = ALLOWED_JWS_ALGS,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Verify a compact JWS against public JWKs. Returns (header, payload).

    The key is chosen by `kid` when the set has more than one; the key's own `alg`, when present,
    must equal the header's (no cross-algorithm use of one key)."""
    if token.count(".") != 2:
        raise CryptoError("JWS_MALFORMED")
    header = peek_header(token)
    alg = header.get("alg")
    if alg not in algorithms or alg not in ALLOWED_JWS_ALGS:
        raise CryptoError("ALG_NOT_ALLOWED", str(alg))
    if header.get("typ") != typ:
        raise CryptoError("JWS_TYP_INVALID", f"expected {typ}")
    if "jku" in header or "x5u" in header or "x5c" in header:
        raise CryptoError("JWS_HEADER_REJECTED", "remote key references are never followed")
    candidates = (
        [k for k in keys if k.get("kid") == header.get("kid")] if len(keys) > 1 else list(keys)
    )
    if len(candidates) != 1:
        raise CryptoError("KEY_UNKNOWN", str(header.get("kid")))
    jwk = candidates[0]
    if jwk.get("alg") not in (None, alg):
        raise CryptoError("ALG_KEY_MISMATCH")
    key = _import(jwk)  # outside the try: a bad key is its own error, not a bad signature
    try:
        obj = jws.deserialize_compact(token, key, algorithms=[alg])
        payload = json.loads(obj.payload)
    except (JoseError, ValueError) as e:
        raise CryptoError("SIGNATURE_INVALID") from e
    if not isinstance(payload, dict):
        raise CryptoError("JWS_MALFORMED", "payload must be a JSON object")
    return header, payload


def _import_external(jwk: Mapping[str, Any]) -> ECKey | OKPKey | RSAKey:
    if jwk.get("kty") == "RSA":
        if any(k in jwk for k in ("d", "p", "q", "dp", "dq", "qi")):
            raise CryptoError("JWK_PRIVATE_MATERIAL", "a public key must not carry private members")
        n = jwk.get("n")
        if not isinstance(n, str) or len(b64url_decode(n)) * 8 < MIN_RSA_BITS:
            raise CryptoError("JWK_UNSUPPORTED", f"RSA keys must be at least {MIN_RSA_BITS} bits")
        return RSAKey.import_key(dict(jwk))
    return _import(jwk)


def verify_external_jws(
    token: str,
    *,
    keys: Sequence[Mapping[str, Any]],
    algorithms: Sequence[str],
    typ: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Verify a JWS signed by an external party with keys pinned for that integration.

    `algorithms` is the integration's own allowlist (always a subset of EXTERNAL_VERIFY_ALGS);
    `typ`, when given, must match exactly (SETs, for example, are always explicitly typed)."""
    if token.count(".") != 2:
        raise CryptoError("JWS_MALFORMED")
    header = peek_header(token)
    alg = header.get("alg")
    if alg not in algorithms or alg not in EXTERNAL_VERIFY_ALGS:
        raise CryptoError("ALG_NOT_ALLOWED", str(alg))
    if typ is not None and header.get("typ") != typ:
        raise CryptoError("JWS_TYP_INVALID", f"expected {typ}")
    if "jku" in header or "x5u" in header or "jwk" in header:
        raise CryptoError("JWS_HEADER_REJECTED", "remote or embedded keys are never trusted")
    kid = header.get("kid")
    candidates = [k for k in keys if k.get("kid") == kid] if kid is not None else list(keys)
    candidates = [k for k in candidates if k.get("use") in (None, "sig")]
    if len(candidates) != 1:
        raise CryptoError("KEY_UNKNOWN", str(kid))
    jwk = candidates[0]
    if jwk.get("alg") not in (None, alg):
        raise CryptoError("ALG_KEY_MISMATCH")
    key = _import_external(jwk)
    try:
        obj = jws.deserialize_compact(token, key, algorithms=[alg])
        payload = json.loads(obj.payload)
    except (JoseError, ValueError) as e:
        raise CryptoError("SIGNATURE_INVALID") from e
    if not isinstance(payload, dict):
        raise CryptoError("JWS_MALFORMED", "payload must be a JSON object")
    return header, payload


@dataclass
class KeyRing:
    """Active signer plus public keys still accepted for verification (rotation window)."""

    active: Signer
    retiring: list[dict[str, Any]] = field(default_factory=list)

    def rotate(self, new: Signer) -> None:
        self.retiring = [self.active.public_jwk(), *self.retiring][:2]
        self.active = new

    def jwks(self) -> dict[str, Any]:
        return {"keys": [self.active.public_jwk(), *self.retiring]}

    def sign(self, payload: Mapping[str, Any], *, typ: str) -> str:
        return sign_jws(self.active, payload, typ=typ)

    def verify(self, token: str, *, typ: str) -> tuple[dict[str, Any], dict[str, Any]]:
        return verify_jws(token, keys=self.jwks()["keys"], typ=typ)


__all__ = [
    "ALLOWED_JWS_ALGS",
    "EXTERNAL_VERIFY_ALGS",
    "CryptoError",
    "KeyRing",
    "LocalSigner",
    "Signer",
    "b64url",
    "b64url_decode",
    "jwk_thumbprint",
    "peek_header",
    "sign_jws",
    "verify_external_jws",
    "verify_jws",
]
