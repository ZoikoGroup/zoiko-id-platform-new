"""Envelope encryption (DEC-F5 §3; ZID-CRYPTO-MI-001 §1.1, §5, §6, §19).

    envelope = await seal(plaintext, aad, provider, kek_ref)
    plaintext = await open_envelope(envelope, aad, provider)   # authorize the caller first

- AES-256-GCM with a fresh random DEK per envelope and a 96-bit random nonce.
- AAD = canonical encoding of tenant | object_type | object_id | field/purpose | schema_version, and
  the provider's encryption context is the same fields, so a row copied to another tenant or object
  fails to unwrap (context) and to decrypt (AAD).
- Stored with the ciphertext: format version, algorithm, KEK purpose/id/version, wrapped DEK, nonce,
  ciphertext||tag. The stored algorithm is checked, never used to choose one.
- Plaintext DEKs live in a bytearray and are overwritten after use (best effort).
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from zid_crypto import CryptoError, b64url, b64url_decode
from zid_crypto.policy import CryptoPolicyRegistry, default_registry
from zid_crypto.providers import NONCE_BYTES, KeyProvider, KeyRef, zeroize
from zid_crypto.purposes import WRAPPING_PURPOSES, KeyPurpose, require_purpose

ENVELOPE_VERSION = 1
ALGORITHM = "AES-256-GCM"
_AAD_LABEL = b"zid-aad-v1"


@dataclass(frozen=True, slots=True)
class Aad:
    """Who/what a ciphertext belongs to. Never put secrets or raw PII here (§19)."""

    tenant_id: str
    object_type: str
    object_id: str
    field: str  # field or purpose
    schema_version: str

    def __post_init__(self) -> None:
        for name in ("tenant_id", "object_type", "object_id", "field", "schema_version"):
            if not isinstance(getattr(self, name), str) or not getattr(self, name):
                raise CryptoError("CRYPTO_KEY_CONTEXT_MISMATCH", f"aad.{name} is required")

    def encode(self) -> bytes:
        """Length-prefixed fields: unambiguous whatever characters the values contain."""
        out = bytearray(_AAD_LABEL)
        for value in self.fields():
            raw = value.encode("utf-8")
            out += len(raw).to_bytes(4, "big") + raw
        return bytes(out)

    def fields(self) -> tuple[str, str, str, str, str]:
        return (self.tenant_id, self.object_type, self.object_id, self.field, self.schema_version)

    def context(self) -> dict[str, str]:
        """The KMS encryption context: exactly the AAD fields."""
        return {
            "tenant": self.tenant_id,
            "object_type": self.object_type,
            "object_id": self.object_id,
            "field": self.field,
            "schema_version": self.schema_version,
        }


@dataclass(frozen=True, slots=True)
class Envelope:
    kek: KeyRef  # exact version
    wrapped_dek: bytes
    nonce: bytes
    ciphertext: bytes  # ciphertext || 16-byte tag
    alg: str = ALGORITHM
    version: int = ENVELOPE_VERSION

    def to_json(self) -> str:
        return json.dumps(
            {
                "v": self.version,
                "alg": self.alg,
                "kek": {
                    "purpose": self.kek.purpose.value,
                    "id": self.kek.key_id,
                    "version": self.kek.version,
                },
                "wdek": b64url(self.wrapped_dek),
                "iv": b64url(self.nonce),
                "ct": b64url(self.ciphertext),
            },
            separators=(",", ":"),
        )

    def to_bytes(self) -> bytes:
        return self.to_json().encode("utf-8")

    @classmethod
    def from_json(cls, text: str | bytes) -> Envelope:
        try:
            doc: Any = json.loads(text)
            if not isinstance(doc, dict):
                raise TypeError("not an object")
            version = doc["v"]
            if version != ENVELOPE_VERSION:
                raise CryptoError("CRYPTO_ENVELOPE_VERSION_UNSUPPORTED", str(version))
            kek = doc["kek"]
            if not isinstance(kek["version"], int) or not isinstance(kek["id"], str):
                raise TypeError("kek")
            envelope = cls(
                kek=KeyRef(KeyPurpose(kek["purpose"]), kek["id"], kek["version"]),
                wrapped_dek=b64url_decode(doc["wdek"]),
                nonce=b64url_decode(doc["iv"]),
                ciphertext=b64url_decode(doc["ct"]),
                alg=str(doc["alg"]),
                version=version,
            )
        except CryptoError:
            raise
        except (ValueError, TypeError, KeyError) as e:
            raise CryptoError("CRYPTO_ENVELOPE_MALFORMED") from e
        if len(envelope.nonce) != NONCE_BYTES or len(envelope.ciphertext) < 16:
            raise CryptoError("CRYPTO_ENVELOPE_MALFORMED", "nonce/tag length")
        return envelope

    from_bytes = from_json


def _check_alg(alg: str, registry: CryptoPolicyRegistry) -> None:
    registry.require(KeyPurpose.DATA_DEK, alg)
    if alg != ALGORITHM:  # the only AEAD implemented here; a stored value never selects another
        raise CryptoError("CRYPTO_ALGORITHM_NOT_ALLOWED", alg)


async def seal(
    plaintext: bytes,
    aad: Aad,
    provider: KeyProvider,
    kek_ref: KeyRef,
    *,
    registry: CryptoPolicyRegistry | None = None,
) -> Envelope:
    require_purpose(kek_ref.purpose, *WRAPPING_PURPOSES)
    _check_alg(ALGORITHM, registry or default_registry())
    data_key = await provider.generate_data_key(kek_ref, aad.context())
    try:
        nonce = secrets.token_bytes(NONCE_BYTES)
        ciphertext = AESGCM(data_key.plaintext).encrypt(nonce, plaintext, aad.encode())
    finally:
        data_key.zeroize()
    return Envelope(
        kek=data_key.kek, wrapped_dek=data_key.wrapped, nonce=nonce, ciphertext=ciphertext
    )


async def open_envelope(
    envelope: Envelope,
    aad: Aad,
    provider: KeyProvider,
    *,
    registry: CryptoPolicyRegistry | None = None,
) -> bytes:
    """Decrypt after the caller is authorized. Wrong AAD/context fails; destroyed KEK raises
    KeyDestroyedError (KEY_DESTROYED)."""
    if envelope.version != ENVELOPE_VERSION:
        raise CryptoError("CRYPTO_ENVELOPE_VERSION_UNSUPPORTED", str(envelope.version))
    _check_alg(envelope.alg, registry or default_registry())
    require_purpose(envelope.kek.purpose, *WRAPPING_PURPOSES)
    dek = await provider.unwrap_data_key(envelope.kek, envelope.wrapped_dek, aad.context())
    try:
        return AESGCM(dek).decrypt(envelope.nonce, envelope.ciphertext, aad.encode())
    except InvalidTag as e:
        raise CryptoError("CRYPTO_AUTHENTICATION_TAG_INVALID") from e
    finally:
        zeroize(dek)


__all__ = ["ALGORITHM", "ENVELOPE_VERSION", "Aad", "Envelope", "open_envelope", "seal"]
