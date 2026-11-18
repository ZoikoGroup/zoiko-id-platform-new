"""Is this registry extract genuine? Signed extracts and QR codes (ZID-DTV-WF-02 K03, section 20; acceptance T06, T07). Pure domain, no I/O.

An extract the applicant uploads proves nothing by looking right. Two things can make it count as the ISSUER's word:

- a signature that verifies against a key the issuer is trusted to use, with an algorithm on the allowed list, inside the key's validity and not revoked. The key material comes from the approved trust
  list, never from the file itself; an unknown issuer, a signature that does not verify, a weak algorithm or a revoked or expired key is `NOT_PROVEN`, not a pass;
- a QR code whose destination is, exactly, a host the issuer is recorded as using (https, no credentials in the address, no raw IP, no look-alike host), AND whose fetched content matches the extract
  by digest. A QR that resolves anywhere else (a mirror, a shortener, a phishing page) is no proof. Fetching is the caller's job through an approved adapter; this module judges the destination and the match.

Anything not proven goes to a human reviewer; nothing here ever rejects a company, and nothing here marks one verified: it only says whether this file is the issuer's word.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from urllib.parse import urlsplit

ALLOWED_ALGORITHMS = frozenset(
    {"Ed25519", "ES256", "PS256"}
)  # no RS256 with PKCS#1 v1.5, no SHA-1, no MD5, no "none"


class Authenticity(StrEnum):
    ISSUER_SIGNED = "ISSUER_SIGNED"  # a trusted key verified the signature
    ISSUER_CONFIRMED_BY_QR = (
        "ISSUER_CONFIRMED_BY_QR"  # the issuer's own destination returned this exact content
    )
    NOT_PROVEN = "NOT_PROVEN"  # to a human reviewer: no issuer-verified assertion
    CONTENT_MISMATCH = "CONTENT_MISMATCH"  # the issuer's destination returned something else: the file differs from the issuer's record


@dataclass(frozen=True, slots=True)
class TrustedKey:
    key_id: str
    algorithm: str
    public_key_pem: bytes
    valid_from: datetime
    valid_to: datetime | None = None
    revoked_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class Issuer:
    issuer_id: str
    keys: tuple[TrustedKey, ...] = ()
    trusted_hosts: frozenset[str] = frozenset()  # exact hostnames, lower case; no wildcard
    path_prefixes: tuple[str, ...] = ("/",)


@dataclass(frozen=True, slots=True)
class Finding:
    result: Authenticity
    reasons: tuple[str, ...]


# Checking a signature needs a cryptography library, and the domain stays pure: it is handed the check. The production one is infrastructure/extract_verifier.py.
Verifier = Callable[[TrustedKey, bytes, bytes], bool]


def verify_signed_extract(
    payload: bytes,
    signature: bytes,
    *,
    claimed_issuer: str,
    key_id: str,
    algorithm: str,
    issuers: Mapping[str, Issuer],
    now: datetime,
    verifier: Verifier,
) -> Finding:
    """The extract is the issuer's word only if a key from the TRUST LIST verifies it. The file's own claims about who signed it are inputs to look up, never evidence."""
    issuer = issuers.get(claimed_issuer)
    if issuer is None:
        return Finding(Authenticity.NOT_PROVEN, ("ISSUER_NOT_TRUSTED",))
    if algorithm not in ALLOWED_ALGORITHMS:
        return Finding(Authenticity.NOT_PROVEN, ("ALGORITHM_NOT_ALLOWED",))
    key = next((k for k in issuer.keys if k.key_id == key_id), None)
    if key is None:
        return Finding(Authenticity.NOT_PROVEN, ("KEY_NOT_IN_TRUST_LIST",))
    if key.algorithm != algorithm:
        return Finding(Authenticity.NOT_PROVEN, ("ALGORITHM_MISMATCH",))
    if key.revoked_at is not None and key.revoked_at <= now:
        return Finding(Authenticity.NOT_PROVEN, ("KEY_REVOKED",))
    if now < key.valid_from or (key.valid_to is not None and now >= key.valid_to):
        return Finding(Authenticity.NOT_PROVEN, ("KEY_NOT_VALID_NOW",))
    if not payload or not signature:
        return Finding(Authenticity.NOT_PROVEN, ("SIGNATURE_MISSING",))
    if not verifier(key, payload, signature):
        return Finding(Authenticity.NOT_PROVEN, ("SIGNATURE_DOES_NOT_VERIFY",))
    return Finding(Authenticity.ISSUER_SIGNED, ())


def qr_destination_problem(url: str, issuer: Issuer) -> str | None:
    """None when the address is, exactly, one the issuer is recorded as using; otherwise why not. The check is on the host and path, never on how the code looks."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return "QR_URL_UNREADABLE"
    if parts.scheme != "https":
        return "QR_NOT_HTTPS"
    if parts.username or parts.password:
        return (
            "QR_HAS_CREDENTIALS_IN_ADDRESS"  # a classic disguise: https://issuer.gov@evil.example/
        )
    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        return "QR_URL_UNREADABLE"
    try:
        ipaddress.ip_address(host)
        return "QR_HOST_IS_AN_IP_ADDRESS"
    except ValueError:
        pass
    try:
        host = host.encode("ascii").decode("ascii")
    except UnicodeError:
        return "QR_HOST_NOT_ASCII"  # an internationalised look-alike is compared only in its xn-- form, which must itself be listed
    if parts.port not in (None, 443):
        return "QR_UNEXPECTED_PORT"
    if host not in issuer.trusted_hosts:
        return "QR_HOST_NOT_THE_ISSUERS"
    if not any((parts.path or "/").startswith(p) for p in issuer.path_prefixes):
        return "QR_PATH_NOT_THE_ISSUERS"
    return None


def judge_qr(
    url: str, issuer: Issuer | None, *, extract_digest: str, fetched_digest: str | None
) -> Finding:
    """The destination must be the issuer's, and what it returns must be this extract. A QR that points anywhere else is no proof (T07); a destination that is the issuer's but returns
    something different says the file is not the issuer's record. Not fetched (the issuer was down) is not proven, never a finding about the file."""
    if issuer is None:
        return Finding(Authenticity.NOT_PROVEN, ("ISSUER_NOT_TRUSTED",))
    problem = qr_destination_problem(url, issuer)
    if problem:
        return Finding(Authenticity.NOT_PROVEN, (problem,))
    if fetched_digest is None:
        return Finding(Authenticity.NOT_PROVEN, ("ISSUER_DESTINATION_UNAVAILABLE",))
    if fetched_digest != extract_digest:
        return Finding(Authenticity.CONTENT_MISMATCH, ("ISSUER_RECORD_DIFFERS_FROM_FILE",))
    return Finding(Authenticity.ISSUER_CONFIRMED_BY_QR, ())
