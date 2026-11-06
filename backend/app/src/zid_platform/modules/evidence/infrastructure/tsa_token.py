"""The platform's use of `zid_evidence.timestamp` (DEC-F2 §1, §3): a timestamp is stored as an anchor only when it is proved.

The validation itself (token, signature, chain to a configured root, revocation) lives in the `zid-evidence` library so an
outside auditor can run the same checks offline. This module adds what the platform needs on top: the trust list from
configuration, CRLs fetched from the certificates' own distribution points, and errors that travel the same road as a TSA
outage (`TimestampRejected` is a `TimestampError`: the checkpoint is sealed unanchored and anchoring is retried).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cryptography import x509
from zid_evidence import Digest
from zid_evidence.timestamp import (
    Revocation,
    TimestampInvalid,
    VerifiedTimestamp,
    check_revocation,
    crl_urls,
    verify_timestamp_ref,
    verify_timestamp_response,
)

from zid_platform.modules.evidence.infrastructure.crl import CrlFetcher, CrlSource
from zid_platform.modules.evidence.infrastructure.tsa import TimestampError

__all__ = [
    "TimestampRejected",
    "TimestampVerifier",
    "VerifiedTimestamp",
    "load_trust_roots",
    "timestamp_verifier_from_settings",
]


class TimestampRejected(TimestampError):
    """The TSA answered, but the answer does not prove a timestamp over our digest. Treated as "not anchored"."""


def load_trust_roots(path: str | Path) -> tuple[x509.Certificate, ...]:
    """The configured trust list: one or more PEM certificates. An empty or unreadable file is an error, never "trust nothing"."""
    try:
        roots = tuple(x509.load_pem_x509_certificates(Path(path).read_bytes()))
    except (OSError, ValueError) as e:
        raise TimestampRejected(f"TSA trust list cannot be read: {type(e).__name__}") from e
    if not roots:
        raise TimestampRejected("TSA trust list holds no certificates")
    return roots


class TimestampVerifier:
    """Verifies timestamp responses and stored references against the trust list, then checks revocation."""

    def __init__(
        self,
        roots: Sequence[x509.Certificate],
        *,
        revocation: Revocation = "if_available",
        crls: CrlSource | None = None,
    ) -> None:
        if not roots:
            raise ValueError("a timestamp verifier needs a TSA trust list")
        self.roots = tuple(roots)
        self.revocation = revocation
        self._crls: CrlSource = crls or CrlFetcher()

    async def verify_response(
        self, der: bytes, digest: Digest, *, nonce: int | None = None, now: datetime | None = None
    ) -> VerifiedTimestamp:
        try:
            verified = verify_timestamp_response(der, digest, self.roots, nonce=nonce, now=now)
        except TimestampInvalid as e:
            raise TimestampRejected(str(e)) from e
        return await self._revocation(verified, now)

    async def verify_ref(
        self, ref: str, digest: Digest, *, now: datetime | None = None
    ) -> VerifiedTimestamp:
        try:
            verified = verify_timestamp_ref(ref, digest, self.roots, now=now)
        except TimestampInvalid as e:
            raise TimestampRejected(str(e)) from e
        return await self._revocation(verified, now)

    async def _revocation(
        self, verified: VerifiedTimestamp, now: datetime | None
    ) -> VerifiedTimestamp:
        if self.revocation == "off":
            return verified
        urls = [
            u for cert in verified.chain[:-1] for u in crl_urls(cert)
        ]  # verified certificates only, never the root
        crls = await self._crls.fetch(urls) if urls else []
        try:
            return check_revocation(
                verified, crls, mode=self.revocation, now=now or datetime.now(UTC)
            )
        except TimestampInvalid as e:
            raise TimestampRejected(str(e)) from e


def timestamp_verifier_from_settings(settings: Any) -> TimestampVerifier | None:
    """The verifier the settings describe, or None when no TSA trust list is configured (timestamps are then not verified)."""
    if not settings.evidence_tsa_trust_file:
        return None
    return TimestampVerifier(
        load_trust_roots(settings.evidence_tsa_trust_file),
        revocation=settings.evidence_tsa_revocation,
    )
