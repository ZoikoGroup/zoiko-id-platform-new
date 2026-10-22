"""Key purposes (DEC-F5 §2; ZID-CRYPTO-MI-001 §3). Purposes never share keys.

Every key handle carries exactly one purpose; an operation that needs a different purpose fails
with CRYPTO_KEY_PURPOSE_MISMATCH instead of reinterpreting the key (§4.1).
"""

from __future__ import annotations

from enum import StrEnum

from zid_crypto import CryptoError


class KeyPurpose(StrEnum):
    DATA_KEK = "DATA_KEK"
    DATA_DEK = "DATA_DEK"
    SUBJECT_ERASURE_KEY = "SUBJECT_ERASURE_KEY"
    TOKEN_SIGNING = "TOKEN_SIGNING"
    FEDERATION_SIGNING = "FEDERATION_SIGNING"
    VC_ISSUER_SIGNING = "VC_ISSUER_SIGNING"
    EVIDENCE_SIGNING = "EVIDENCE_SIGNING"
    WEBHOOK_SIGNING = "WEBHOOK_SIGNING"
    CA = "CA"
    HMAC_INTERNAL = "HMAC_INTERNAL"
    BACKUP_KEK = "BACKUP_KEK"
    SECRET_ENCRYPTION = "SECRET_ENCRYPTION"


# Purposes whose keys wrap data keys in envelope encryption (the KEK side of `seal`).
WRAPPING_PURPOSES = frozenset(
    {
        KeyPurpose.DATA_KEK,
        KeyPurpose.SUBJECT_ERASURE_KEY,
        KeyPurpose.BACKUP_KEK,
        KeyPurpose.SECRET_ENCRYPTION,
    }
)


def require_purpose(actual: KeyPurpose, *expected: KeyPurpose) -> None:
    """Raise CRYPTO_KEY_PURPOSE_MISMATCH unless `actual` is one of `expected`."""
    if actual not in expected:
        allowed = ", ".join(p.value for p in expected)
        raise CryptoError("CRYPTO_KEY_PURPOSE_MISMATCH", f"{actual.value} used for {allowed}")


__all__ = ["WRAPPING_PURPOSES", "KeyPurpose", "require_purpose"]
