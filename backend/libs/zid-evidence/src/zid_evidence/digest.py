"""Digests that carry their algorithm id (EVIDENCE-001 §5.1; DEC-F2 §1).

Every digest is stored and exchanged as `<alg>:<lowercase hex>`, e.g. `sha-256:9f86...`. There is
no implicit global hash: every hashing call names its algorithm, and parsing refuses an algorithm
outside the allowlist or a value of the wrong length. SHA-256 is the required baseline; SHA-384
and SHA-512 are allowed for stronger profiles and hash migration (new attestations over existing
digests, never rewritten payloads).
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from zid_evidence.jcs import canonicalize

__all__ = ["HASH_ALGS", "REQUIRED_ALG", "Digest", "DigestError", "hash_bytes", "hash_json"]

REQUIRED_ALG = "sha-256"
HASH_ALGS: dict[str, Callable[[bytes], Any]] = {
    "sha-256": hashlib.sha256,
    "sha-384": hashlib.sha384,
    "sha-512": hashlib.sha512,
}
_SIZES = {"sha-256": 32, "sha-384": 48, "sha-512": 64}


class DigestError(ValueError):
    code = "EVIDENCE_SCHEMA_INVALID"


def _check_alg(alg: str) -> None:
    if alg not in HASH_ALGS:
        raise DigestError(f"hash algorithm not allowed: {alg!r}")


@dataclass(frozen=True, slots=True)
class Digest:
    alg: str
    value: bytes

    def __post_init__(self) -> None:
        _check_alg(self.alg)
        if len(self.value) != _SIZES[self.alg]:
            raise DigestError(f"{self.alg} digest must be {_SIZES[self.alg]} bytes")

    @classmethod
    def parse(cls, text: str) -> Digest:
        """`sha-256:<hex>` -> Digest. The algorithm id is mandatory."""
        if not isinstance(text, str) or ":" not in text:
            raise DigestError("digest must be '<alg>:<hex>' with an explicit algorithm id")
        alg, _, hexval = text.partition(":")
        _check_alg(alg)
        if hexval != hexval.lower():
            raise DigestError("digest hex must be lowercase")
        try:
            value = bytes.fromhex(hexval)
        except ValueError as e:
            raise DigestError("digest value is not hex") from e
        return cls(alg, value)

    def __str__(self) -> str:
        return f"{self.alg}:{self.value.hex()}"

    def matches(self, other: Digest) -> bool:
        """Constant-time equality (same algorithm and value)."""
        return self.alg == other.alg and hmac.compare_digest(self.value, other.value)


def hash_bytes(data: bytes, *, alg: str) -> Digest:
    _check_alg(alg)
    return Digest(alg, HASH_ALGS[alg](data).digest())


def hash_json(value: Any, *, alg: str) -> Digest:
    """Digest of the RFC 8785 canonical form of a JSON value."""
    return hash_bytes(canonicalize(value), alg=alg)
