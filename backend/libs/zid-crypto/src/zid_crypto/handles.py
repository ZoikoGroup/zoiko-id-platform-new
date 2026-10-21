"""Pseudonymous handles (DEC-F5; DEC-F1/F2 evidence minimization).

    handle(key, tenant_id, domain, value) -> "ph1.<key version>.<base64url HMAC-SHA-256>"

Keyed HMAC under an HMAC_INTERNAL key, never an unsalted hash: without the key a handle cannot be
recomputed from a guessed value. The tenant and the evidence domain are part of the MAC input, so the
same value gets unrelated handles in another tenant or domain. Handles are deterministic per
(key version, tenant, domain, value), and the key version is in the handle so rotation is explicit.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass, field
from typing import Protocol

from zid_crypto import CryptoError, b64url
from zid_crypto.purposes import KeyPurpose, require_purpose

HANDLE_PREFIX = "ph1"
_LABEL = b"zid-handle-v1"


class MacKey(Protocol):
    @property
    def purpose(self) -> KeyPurpose: ...
    @property
    def version(self) -> int: ...
    def mac(self, message: bytes) -> bytes: ...


@dataclass(frozen=True, slots=True)
class LocalHmacKey:
    """In-process HMAC-SHA-256 key (local/test, or material released by the broker in memory)."""

    version: int
    secret: bytes = field(repr=False)
    purpose: KeyPurpose = KeyPurpose.HMAC_INTERNAL

    def __post_init__(self) -> None:
        if len(self.secret) < 32:
            raise CryptoError("CRYPTO_KEY_STRENGTH", "HMAC keys need at least 256 bits")

    def mac(self, message: bytes) -> bytes:
        return hmac.new(self.secret, message, hashlib.sha256).digest()


def _message(tenant_id: str, domain: str, value: str) -> bytes:
    out = bytearray(_LABEL)
    for part in (tenant_id, domain, value):
        if not isinstance(part, str) or not part:
            raise CryptoError(
                "CRYPTO_KEY_CONTEXT_MISMATCH", "tenant, domain and value are required"
            )
        raw = part.encode("utf-8")
        out += len(raw).to_bytes(4, "big") + raw
    return bytes(out)


def handle(key: MacKey, tenant_id: str, domain: str, value: str) -> str:
    require_purpose(key.purpose, KeyPurpose.HMAC_INTERNAL)
    return f"{HANDLE_PREFIX}.{key.version}.{b64url(key.mac(_message(tenant_id, domain, value)))}"


def handle_key_version(value: str) -> int:
    """The key version a handle was made with (to pick the key that recomputes it)."""
    prefix, version, _ = (value.split(".") + ["", ""])[:3]
    if prefix != HANDLE_PREFIX or not version.isdigit():
        raise CryptoError("CRYPTO_HANDLE_MALFORMED")
    return int(version)


__all__ = ["HANDLE_PREFIX", "LocalHmacKey", "MacKey", "handle", "handle_key_version"]
