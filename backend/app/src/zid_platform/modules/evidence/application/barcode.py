"""Signed 2D barcode payloads: a COSE_Sign1 inside base45 and zlib, the EU digital certificate shape (CR-073 P2-4 item 2). No network, no files.

What this establishes, and what it does not:
- it decodes the text a QR code carries (`HC1:` prefix, base45, zlib with a size cap) and reads the COSE_Sign1 inside it;
- it verifies the signature ONLY against keys the operator configured (certificates in `config/trust/barcode-keys/`), selected by the key id the
  payload names; a payload signed by a key nobody configured is INDETERMINATE (`KEY_NOT_CONFIGURED`), never VERIFIED, and a payload whose signature
  fails against the configured key is NOT_VERIFIED;
- the payload is returned as it was signed, with bytes shown as hex. A valid signature says that holder of the configured key signed these
  bytes. It does not say the person presenting the barcode is the person it describes (that is holder binding), and it does not say the claims are
  true, current, or lawful authority for any purpose;
- an unsigned payload, or a signature algorithm outside ES256 and PS256, is never accepted.

Algorithms: ES256 (ECDSA P-256 with SHA-256, signature as r||s) and PS256 (RSA-PSS, SHA-256, MGF1 SHA-256, salt length 32).
"""

from __future__ import annotations

import hashlib
import zlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import cbor2
from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

PREFIX = "HC1:"
MAX_INFLATED = 1024 * 1024
MAX_TEXT = 8192
_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ $%*+-./:"
COSE_SIGN1_TAG = 18
ALG_ES256, ALG_PS256 = -7, -37


class BarcodeError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


def base45_decode(text: str) -> bytes:
    """RFC 9285. Groups of three characters make two bytes, a final group of two makes one; a value too big for its group is refused."""
    if len(text) % 3 == 1:
        raise BarcodeError("BASE45_LENGTH_INVALID")
    out = bytearray()
    for i in range(0, len(text), 3):
        chunk = text[i : i + 3]
        try:
            vals = [_ALPHABET.index(c) for c in chunk]
        except ValueError as e:
            raise BarcodeError("BASE45_CHARACTER_INVALID") from e
        if len(chunk) == 3:
            n = vals[0] + vals[1] * 45 + vals[2] * 45 * 45
            if n > 0xFFFF:
                raise BarcodeError("BASE45_VALUE_TOO_LARGE")
            out += n.to_bytes(2, "big")
        else:
            n = vals[0] + vals[1] * 45
            if n > 0xFF:
                raise BarcodeError("BASE45_VALUE_TOO_LARGE")
            out.append(n)
    return bytes(out)


def inflate(data: bytes) -> bytes:
    d = zlib.decompressobj()
    try:
        out = d.decompress(data, MAX_INFLATED + 1)
    except zlib.error as e:
        raise BarcodeError("PAYLOAD_NOT_COMPRESSED_DATA") from e
    if len(out) > MAX_INFLATED:
        raise BarcodeError("PAYLOAD_TOO_LARGE")
    return out


@dataclass(frozen=True, slots=True)
class CoseSign1:
    protected_raw: bytes
    protected: dict[Any, Any]
    unprotected: dict[Any, Any]
    payload: bytes
    signature: bytes

    @property
    def algorithm(self) -> int | None:
        alg = self.protected.get(1, self.unprotected.get(1))
        return alg if isinstance(alg, int) else None

    @property
    def key_id(self) -> bytes | None:
        kid = self.protected.get(4, self.unprotected.get(4))
        return kid if isinstance(kid, bytes) else None


def read_cose(raw: bytes) -> CoseSign1:
    try:
        obj = cbor2.loads(raw)
    except (cbor2.CBORDecodeError, ValueError, RecursionError, MemoryError, OverflowError) as e:
        raise BarcodeError("COSE_NOT_READABLE") from e
    if isinstance(obj, cbor2.CBORTag):
        if obj.tag != COSE_SIGN1_TAG:
            raise BarcodeError("COSE_TAG_UNEXPECTED", str(obj.tag))
        obj = obj.value
    # cbor2 hands back a tuple for an array and an immutable mapping for a map once inside a tag, so accept the read-only forms too
    if not (isinstance(obj, list | tuple) and len(obj) == 4):
        raise BarcodeError("COSE_SIGN1_SHAPE_INVALID")
    protected_raw, unprotected, payload, signature = obj
    if not (
        isinstance(protected_raw, bytes)
        and isinstance(unprotected, Mapping)
        and isinstance(payload, bytes)
        and isinstance(signature, bytes)
    ):
        raise BarcodeError("COSE_SIGN1_SHAPE_INVALID")
    try:
        protected = cbor2.loads(protected_raw) if protected_raw else {}
    except (cbor2.CBORDecodeError, ValueError) as e:
        raise BarcodeError("COSE_PROTECTED_NOT_READABLE") from e
    if not isinstance(protected, Mapping):
        raise BarcodeError("COSE_PROTECTED_NOT_A_MAP")
    return CoseSign1(protected_raw, dict(protected), dict(unprotected), payload, signature)


def decode_text(text: str) -> CoseSign1:
    """The text a QR code carries to the COSE_Sign1 it holds."""
    t = text.strip()
    if len(t) > MAX_TEXT:
        raise BarcodeError("PAYLOAD_TOO_LARGE")
    if not t.startswith(PREFIX):
        raise BarcodeError("PREFIX_NOT_RECOGNISED")
    return read_cose(inflate(base45_decode(t[len(PREFIX) :])))


def key_id_of(cert: x509.Certificate) -> bytes:
    """The key id the EU scheme uses: the first eight bytes of the SHA-256 of the certificate."""
    from cryptography.hazmat.primitives.serialization import Encoding

    return hashlib.sha256(cert.public_bytes(Encoding.DER)).digest()[:8]


@dataclass(frozen=True, slots=True)
class Verdict:
    status: str  # VERIFIED | NOT_VERIFIED | INDETERMINATE
    reasons: tuple[str, ...]
    key_id_hex: str | None = None
    signer: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)


def verify(msg: CoseSign1, keys: list[x509.Certificate], *, at: datetime | None = None) -> Verdict:
    when = at or datetime.now(UTC)
    kid = msg.key_id
    kid_hex = kid.hex() if kid else None
    alg = msg.algorithm
    if alg not in {ALG_ES256, ALG_PS256}:
        return Verdict("INDETERMINATE", ("ALGORITHM_NOT_SUPPORTED",), kid_hex)
    if kid is None:
        return Verdict("INDETERMINATE", ("KEY_ID_MISSING",), kid_hex)
    cert = next((c for c in keys if key_id_of(c) == kid), None)
    if cert is None:
        return Verdict("INDETERMINATE", ("KEY_NOT_CONFIGURED",), kid_hex)
    signer = cert.subject.rfc4514_string()
    if not (cert.not_valid_before_utc <= when <= cert.not_valid_after_utc):
        return Verdict("INDETERMINATE", ("KEY_CERTIFICATE_NOT_VALID_NOW",), kid_hex, signer)
    data = cbor2.dumps(["Signature1", msg.protected_raw, b"", msg.payload])
    key = cert.public_key()
    try:
        if alg == ALG_ES256 and isinstance(key, ec.EllipticCurvePublicKey):
            if key.curve.name != "secp256r1" or len(msg.signature) != 64:
                return Verdict("NOT_VERIFIED", ("SIGNATURE_SHAPE_INVALID",), kid_hex, signer)
            der = encode_dss_signature(
                int.from_bytes(msg.signature[:32], "big"), int.from_bytes(msg.signature[32:], "big")
            )
            key.verify(der, data, ec.ECDSA(hashes.SHA256()))
        elif alg == ALG_PS256 and isinstance(key, rsa.RSAPublicKey):
            key.verify(
                msg.signature,
                data,
                padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=32),
                hashes.SHA256(),
            )
        else:
            return Verdict("NOT_VERIFIED", ("KEY_DOES_NOT_MATCH_ALGORITHM",), kid_hex, signer)
    except InvalidSignature:
        return Verdict("NOT_VERIFIED", ("SIGNATURE_INVALID",), kid_hex, signer)
    return Verdict("VERIFIED", ("SIGNATURE_VALID_FOR_CONFIGURED_KEY",), kid_hex, signer)


def jsonable(value: Any, depth: int = 0) -> Any:
    """The signed payload as plain data: bytes become hex, tags become their value, anything odd becomes its text. Depth is capped."""
    if depth > 12:
        return "…"
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, cbor2.CBORTag):
        return jsonable(value.value, depth + 1)
    if isinstance(value, Mapping):
        return {str(k): jsonable(v, depth + 1) for k, v in list(value.items())[:200]}
    if isinstance(value, list | tuple):
        return [jsonable(v, depth + 1) for v in list(value)[:200]]
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    return str(value)


def claims_of(msg: CoseSign1) -> dict[str, Any]:
    """The signed payload, as it was signed. Nothing is added to it and nothing is left out."""
    try:
        return {"payload": jsonable(cbor2.loads(msg.payload))}
    except (cbor2.CBORDecodeError, ValueError, RecursionError):
        return {"payload_hex": msg.payload.hex()}
