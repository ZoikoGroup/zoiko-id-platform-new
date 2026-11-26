"""What a public page must never carry (ZID-CP-WF12 v2.0, WF-12.04 disclosure minimization, WF-12.08 and WF-12.07). Pure domain, no I/O.

One scanner serves claims, advisories and the security overview: a secret, a key, a private address, an internal host name or a personal identifier in public text is a block, not a warning.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("PRIVATE_KEY", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("PLATFORM_TOKEN", re.compile(r"\bz(?:vl|at|ps|rt)_[A-Za-z0-9_-]{8,}")),
    ("CLOUD_KEY", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("BEARER_TOKEN", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/-]{16,}")),
    ("JWT", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),
    ("IPV4", re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b")),
    ("IPV6", re.compile(r"\b(?:[0-9a-fA-F]{1,4}:){4,7}[0-9a-fA-F]{1,4}\b")),
    ("CIDR_OR_SUBNET", re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}/\d{1,2}\b")),
    ("INTERNAL_HOST", re.compile(r"(?i)\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:internal|local|corp|lan|intranet)\b")),
    ("EMAIL_ADDRESS", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
    ("LONG_NUMBER", re.compile(r"\b\d{13,19}\b")),
    ("DB_URL", re.compile(r"(?i)\b(?:postgres(?:ql)?|mysql|mongodb|redis)(?:\+\w+)?://\S+")),
)
_EXPLOIT_WORDS = ("proof of concept", "poc exploit", "exploit chain", "payload:", "shellcode", "sql injection in", "bypass the", "cve-2")  # an advisory describes impact, not the way in


def findings(text: str, *, allow: Sequence[str] = ()) -> list[str]:
    """Names of what the text carries that it must not. `allow` lists a pattern a page is expressly permitted (a published contact address)."""
    out = [name for name, rx in _PATTERNS if name not in allow and rx.search(text)]
    low = text.lower()
    if "EXPLOIT_DETAIL" not in allow and any(w in low for w in _EXPLOIT_WORDS):
        out.append("EXPLOIT_DETAIL")
    return out


def safe_for_public(text: str, *, allow: Sequence[str] = ()) -> list[str]:
    return [f"The text carries {f.lower().replace('_', ' ')}; remove it before it is public." for f in findings(text, allow=allow)]


# a public sentence may not claim these without the matching claim class: they are approval-class words handled in claims.CERT_WORDS; these are marketing superlatives a buyer cannot test
VAGUE_SECURITY = ("military-grade", "military grade", "unhackable", "bank-grade", "100% secure", "unbreakable", "impenetrable", "zero risk", "guaranteed secure")


def vague_security_language(text: str) -> list[str]:
    low = text.lower()
    return [w for w in VAGUE_SECURITY if w in low]
