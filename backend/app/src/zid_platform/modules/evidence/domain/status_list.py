"""A signed, privacy-preserving status list for credentials and agents (ZID-CP-WF08 v2.0 E09, section 08.1; CR-170). Pure domain, no I/O.

Modelled on the W3C Bitstring Status List idea: every credential gets one position in a large bit string; a set bit means "revoked" (or "suspended"). The
whole list is published, signed, with a short life. A relying party downloads the list and looks up its own position locally, so the platform never learns
which credential it is checking, and the list is large and its positions random, so a position says nothing about who it belongs to or when it was issued.

Rules this module keeps: a list never shrinks below the herd-privacy minimum; a position is assigned at random, never in order; a list older than the age
the relying party accepts says UNKNOWN, never "not revoked"; revocation is a bit that is set, and reinstating is its own recorded act, never an erasure.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any

SCHEMA = "zoiko.status-list.v1"
TYP = "zid-status-list+jwt"
MIN_BITS = 131072  # 16 KiB, the W3C minimum, so a list is never small enough to single anyone out
MAX_BITS = 8 * 1024 * 1024
PURPOSES = ("revocation", "suspension")
TTL = timedelta(hours=1)
MAX_TTL = timedelta(hours=24)


class StatusListError(ValueError):
    code = "STATUS_LIST_INVALID"


def new_bits(size: int = MIN_BITS) -> bytearray:
    if size < MIN_BITS or size > MAX_BITS or size % 8:
        raise StatusListError("a list is between 131072 and 8388608 bits, in whole bytes")
    return bytearray(size // 8)


def set_bit(bits: bytearray, index: int, value: bool = True) -> None:
    if not 0 <= index < len(bits) * 8:
        raise StatusListError("position outside the list")
    mask = 0x80 >> (index % 8)  # most significant bit first, as the W3C format orders them
    if value:
        bits[index // 8] |= mask
    else:
        bits[index // 8] &= ~mask & 0xFF


def get_bit(bits: bytes | bytearray, index: int) -> bool:
    if not 0 <= index < len(bits) * 8:
        raise StatusListError("position outside the list")
    return bool(bits[index // 8] & (0x80 >> (index % 8)))


def list_claims(
    *,
    list_id: str,
    purpose: str,
    version: int,
    encoded: str,
    size_bits: int,
    issuer: str,
    environment: str,
    now: datetime,
    ttl: timedelta = TTL,
) -> dict[str, Any]:
    if purpose not in PURPOSES:
        raise StatusListError("unknown purpose")
    ttl = min(ttl, MAX_TTL)
    return {
        "schema": SCHEMA,
        "list_id": list_id,
        "purpose": purpose,
        "version": version,
        "size_bits": size_bits,
        "encoding": "base64url(gzip(bitstring))",
        "encoded_list": encoded,
        "issuer": issuer,
        "environment": environment,
        "issued_at": now.isoformat(),
        "expires_at": (now + ttl).isoformat(),
        "limits": "A set bit means the platform marked this credential. An unset bit means it was not marked when the list was issued, not that it is valid now: check the list's age.",
    }


def lookup(
    claims: Mapping[str, Any],
    bits: bytes,
    index: int,
    *,
    now: datetime,
    max_age: timedelta | None,
    expected_issuer: str,
    expected_environment: str,
) -> str:
    """REVOKED / SUSPENDED when the bit is set, ACTIVE when it is not and the list is fresh enough, otherwise UNKNOWN. Wrong issuer or environment, an
    expired list, a list older than the accepted age, a position outside it: all UNKNOWN. Nothing stale is ever read as ACTIVE."""
    try:
        issued, expires = (
            datetime.fromisoformat(str(claims["issued_at"])),
            datetime.fromisoformat(str(claims["expires_at"])),
        )
    except (KeyError, ValueError):
        return "UNKNOWN"
    if (
        claims.get("schema") != SCHEMA
        or claims.get("issuer") != expected_issuer
        or claims.get("environment") != expected_environment
    ):
        return "UNKNOWN"
    if (
        now > expires
        or max_age is None
        or now - issued > max_age
        or int(claims.get("size_bits", 0)) != len(bits) * 8
    ):
        return "UNKNOWN"
    try:
        marked = get_bit(bits, index)
    except StatusListError:
        return "UNKNOWN"
    if marked:
        return "REVOKED" if claims.get("purpose") == "revocation" else "SUSPENDED"
    return "ACTIVE"
