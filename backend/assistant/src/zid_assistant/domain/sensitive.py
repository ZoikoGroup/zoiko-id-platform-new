"""Pre-model, pre-persistence interception of secrets and identity data (ZI-CHAT-ADR-006 ADR-08, INV-03, PR-02).

Runs on the raw text in request memory BEFORE logging, queueing, embedding, retrieval or any model call. It returns only the
*categories* it found, never the matched text, so a caller cannot accidentally log or echo a secret. No detector is perfect:
the aim is a conservative block with a safe explanation, plus a test corpus that proves nothing leaks downstream.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

_ZERO_WIDTH = dict.fromkeys(map(ord, "\u200b‌‍⁠﻿­"))

# Order is irrelevant; every pattern is checked. Patterns are intentionally specific to keep false positives low.
_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("PRIVATE_KEY", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----", re.IGNORECASE)),
    ("JWT", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{4,}\b")),
    (
        "AUTH_HEADER",
        re.compile(r"\b(?:authorization|proxy-authorization)\s*:\s*\S+", re.IGNORECASE),
    ),
    ("BEARER_TOKEN", re.compile(r"\bbearer\s+[A-Za-z0-9._~+/=-]{16,}", re.IGNORECASE)),
    ("COOKIE_HEADER", re.compile(r"\b(?:set-)?cookie\s*:\s*\S+=\S+", re.IGNORECASE)),
    ("API_KEY", re.compile(r"\b(?:sk|pk|rk)[-_](?:live|test|ant|proj)?[-_]?[A-Za-z0-9]{16,}\b")),
    ("API_KEY", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("API_KEY", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr|github_pat)_[A-Za-z0-9_]{20,}\b")),
    ("API_KEY", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("API_KEY", re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b")),
    (
        "PASSWORD",
        re.compile(r"\b(?:password|passwd|passcode|pwd)\b\s*(?:is|=|:)\s*\S{3,}", re.IGNORECASE),
    ),
    (
        "SECRET_ASSIGN",
        re.compile(
            r"\b(?:secret|api[_ -]?key|access[_ -]?token|client[_ -]?secret)\b\s*(?:is|=|:)\s*\S{6,}",
            re.IGNORECASE,
        ),
    ),
    (
        "OTP",
        re.compile(
            r"\b(?:otp|one[- ]time (?:code|password|passcode)|verification code|security code|2fa code|mfa code|sms code)\b[^0-9]{0,24}\b\d{4,8}\b",
            re.IGNORECASE,
        ),
    ),
    (
        "OTP",
        re.compile(
            r"\b\d{6}\b(?=[^.\n]{0,24}\b(?:is my|as my)\b[^.\n]{0,24}\b(?:code|otp)\b)",
            re.IGNORECASE,
        ),
    ),
    (
        "RECOVERY_CODE",
        re.compile(
            r"\b(?:recovery|backup) (?:code|key|phrase)s?\b[^.\n]{0,20}[:=]\s*\S{6,}", re.IGNORECASE
        ),
    ),
    (
        "SEED_PHRASE",
        re.compile(
            r"\b(?:seed|mnemonic|recovery) phrase\b[^.\n]{0,12}[:=]\s*(?:[a-z]{3,8}\s+){8,}",
            re.IGNORECASE,
        ),
    ),
    ("DATA_URL", re.compile(r"\bdata:(?:image|application)/[a-z0-9.+-]+;base64,", re.IGNORECASE)),
    ("URL_CREDENTIALS", re.compile(r"\b[a-z][a-z0-9+.-]*://[^/\s:@]+:[^/\s@]+@", re.IGNORECASE)),
    ("SSN", re.compile(r"\b(?!000|666|9\d\d)\d{3}-(?!00)\d{2}-(?!0000)\d{4}\b")),
    ("AADHAAR", re.compile(r"\b[2-9]\d{3}[ -]?\d{4}[ -]?\d{4}\b")),
    ("PAN", re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")),
    ("PASSPORT", re.compile(r"\bpassport\b[^.\n]{0,24}\b[A-Z0-9]{6,9}\b", re.IGNORECASE)),
    ("IBAN", re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){3,7}(?: ?[A-Z0-9]{1,3})?\b")),
    (
        "GOV_ID",
        re.compile(
            r"\b(?:national id|id number|driver'?s? licen[cs]e|licen[cs]e number|nin|ssn|tax id)\b[^.\n]{0,12}[:#=]\s*[A-Z0-9-]{6,}",
            re.IGNORECASE,
        ),
    ),
    (
        "BIOMETRIC_PAYLOAD",
        re.compile(
            r"\b(?:face|fingerprint|iris|voice)[ -]?(?:template|embedding|hash|vector)\b[^.\n]{0,12}[:=]\s*\S{8,}",
            re.IGNORECASE,
        ),
    ),
)
_CARD = re.compile(r"\b(?:\d[ -]?){13,19}\b")
_LONG_BASE64 = re.compile(r"[A-Za-z0-9+/_-]{180,}={0,2}")
_LONG_HEX = re.compile(r"\b[0-9a-fA-F]{40,}\b")


@dataclass(frozen=True, slots=True)
class Finding:
    """Categories only. Never the matched text (INV-03)."""

    categories: frozenset[str] = field(default_factory=frozenset)

    @property
    def blocked(self) -> bool:
        return bool(self.categories)


def normalize(text: str) -> str:
    """NFKC + drop zero-width characters, so a payload cannot dodge a pattern with invisible characters."""
    return unicodedata.normalize("NFKC", text).translate(_ZERO_WIDTH)


def _luhn(digits: str) -> bool:
    total, alt = 0, False
    for ch in reversed(digits):
        d = ord(ch) - 48
        if alt:
            d *= 2
            d -= 9 if d > 9 else 0
        total += d
        alt = not alt
    return total % 10 == 0


def scan(text: str) -> Finding:
    """Inspect raw text in memory. The caller must discard `text` if the result is blocked."""
    t = normalize(text)
    found: set[str] = set()
    for name, pattern in _PATTERNS:
        if pattern.search(t):
            found.add(name)
    for m in _CARD.finditer(t):
        digits = re.sub(r"\D", "", m.group(0))
        if 13 <= len(digits) <= 19 and _luhn(digits):
            found.add("PAYMENT_CARD")
            break
    if _LONG_BASE64.search(t):
        found.add("ENCODED_BLOB")
    if _LONG_HEX.search(t):
        found.add("HEX_SECRET")
    return Finding(frozenset(found))


_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+\b")
_PHONE = re.compile(r"(?<![\w.])(?:\+\d[\d ()-]{8,16}\d|\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4})(?![\w])")


def scan_chat(*parts: str | None) -> Finding:
    """Chat messages: everything `scan` finds, plus an email address or phone number. The public chat takes no personal details (the
    notice says so); contact details are collected only by the separate, consented handoff form, which uses `scan`."""
    cats = set(scan_many(*parts).categories)
    for p in parts:
        if not p:
            continue
        t = normalize(p)
        if _EMAIL.search(t):
            cats.add("EMAIL")
        if _PHONE.search(t):
            cats.add("PHONE")
    return Finding(frozenset(cats))


def scan_many(*parts: str | None) -> Finding:
    cats: set[str] = set()
    for p in parts:
        if p:
            cats |= scan(p).categories
    return Finding(frozenset(cats))
