"""Continuous evidence monitoring and claim drift (ZID-CP-WF12 v2.0, WF-12.12, TRU-01, TRU-13, TRU-14). Pure domain, no I/O.

A page, a sales document or a structured-data block is read for sentences that sound like a claim; each one must match a live, approved sentence exactly, or it is reported. Nothing is
fixed automatically: the scanner finds, a person with the right capability withdraws or qualifies.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from zid_platform.modules.trust_center.domain import claims as cl

_NUMBER_CLAIM = re.compile(
    r"(?:\b\d{1,3}(?:\.\d+)?\s?%|\b\d+(?:\.\d+)?\s?[KMB]\+|\b\d{2,}\+|<\s?\d+\s?ms\b|\b\d+\s?ms\b)",
    re.IGNORECASE,
)
_CERT = re.compile(
    r"\b(?:soc\s?2|iso\s?27001|fedramp|hipaa|pci[- ]dss|gdpr[- ]compliant|certified|accredited|compliant|qualified)\b",
    re.IGNORECASE,
)
_SECURITY_SUPERLATIVE = re.compile(
    r"\b(?:military[- ]grade|bank[- ]grade|unhackable|100%\s+secure|zero[- ]risk)\b", re.IGNORECASE
)
_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")
_JSONLD = re.compile(
    r"<script[^>]*application/ld\+json[^>]*>(.*?)</script>", re.DOTALL | re.IGNORECASE
)
_BADGE_KEYS = (
    "award",
    "aggregaterating",
    "review",
    "certif",
    "hasCredential".lower(),
    "accreditation",
)


def normalize(s: str) -> str:
    return re.sub(r"[^a-z0-9%+<> ]", "", re.sub(r"\s+", " ", s.lower())).strip()


@dataclass(frozen=True)
class Observed:
    url: str
    sentence: str
    why: str  # NUMBER, CERTIFICATION, SUPERLATIVE or STRUCTURED_DATA


@dataclass(frozen=True)
class Finding:
    url: str
    sentence: str
    code: str  # UNREGISTERED_CLAIM, WITHDRAWN_CLAIM_STILL_LIVE, VAGUE_SECURITY_LANGUAGE
    detail: str


def observe(url: str, html_or_text: str) -> list[Observed]:
    """Every sentence on the page that makes a number, certification or superlative claim, plus any structured-data block that asserts an award, rating or credential."""
    out: list[Observed] = []
    for block in _JSONLD.findall(html_or_text):
        try:
            data = json.loads(block)
        except ValueError:
            out.append(Observed(url, block.strip()[:200], "STRUCTURED_DATA"))
            continue
        flat = json.dumps(data).lower()
        if any(k in flat for k in _BADGE_KEYS):
            out.append(
                Observed(
                    url,
                    f"structured data asserts: {', '.join(k for k in _BADGE_KEYS if k in flat)}",
                    "STRUCTURED_DATA",
                )
            )
    plain = re.sub(
        r"<script.*?</script>|<style.*?</style>", " ", html_or_text, flags=re.DOTALL | re.IGNORECASE
    )
    plain = re.sub(r"<[^>]+>", " ", plain)
    for raw in _SPLIT.split(plain):
        s = raw.strip()
        if len(s) < 6:
            continue
        if _SECURITY_SUPERLATIVE.search(s):
            out.append(Observed(url, s, "SUPERLATIVE"))
        elif _CERT.search(s):
            out.append(Observed(url, s, "CERTIFICATION"))
        elif _NUMBER_CLAIM.search(s):
            out.append(Observed(url, s, "NUMBER"))
    return out


def reconcile(
    observed: Iterable[Observed], live: Sequence[str], withdrawn: Sequence[str]
) -> list[Finding]:
    """Compare what is on the public surfaces to the register. A sentence that matches a live approved one is fine; a sentence that matches a withdrawn one is a defect; anything else claim-like is unregistered."""
    ok = {normalize(t) for t in live}
    gone = {normalize(t) for t in withdrawn}
    out: list[Finding] = []
    for o in observed:
        n = normalize(o.sentence)
        if any(n == k or (len(k) > 20 and k in n) for k in ok):
            continue
        if any(n == k or (len(k) > 20 and k in n) for k in gone):
            out.append(
                Finding(
                    o.url,
                    o.sentence,
                    "WITHDRAWN_CLAIM_STILL_LIVE",
                    "This sentence was withdrawn and is still published here.",
                )
            )
        elif o.why == "SUPERLATIVE":
            out.append(
                Finding(
                    o.url,
                    o.sentence,
                    "VAGUE_SECURITY_LANGUAGE",
                    "A buyer cannot test this; state the control and its scope.",
                )
            )
        else:
            out.append(
                Finding(
                    o.url,
                    o.sentence,
                    "UNREGISTERED_CLAIM",
                    f"A {o.why.lower().replace('_', ' ')} statement with no approved register entry.",
                )
            )
    return out


@dataclass(frozen=True)
class Watch:
    claim_id: str
    revision: int
    reason: str
    days_left: int | None


def expiry_watch(
    live: Sequence[tuple[cl.Claim, date | None]], *, today: date, horizon_days: int = 30
) -> list[Watch]:
    """Live claims whose own date or earliest evidence end falls within the horizon, soonest first. An evidence date already past is reported as 0 days."""
    out: list[Watch] = []
    for c, evidence_end in live:
        for why, d in (("claim valid_until", c.valid_until), ("evidence period end", evidence_end)):
            if d is None:
                continue
            left = (d - today).days
            if left <= horizon_days:
                out.append(Watch(c.claim_id, c.revision, f"{why} {d.isoformat()}", max(left, 0)))
    return sorted(out, key=lambda w: (w.days_left if w.days_left is not None else 9999, w.claim_id))


def release_gate(
    *, unsupported_live: int, overdue_withdrawal_reviews: int, unacknowledged_reports: int
) -> tuple[bool, list[str]]:
    """The wireframe's gate: zero unsupported live claims. Overdue withdrawal reviews and unacknowledged vulnerability reports also hold a release."""
    why = []
    if unsupported_live:
        why.append(f"{unsupported_live} live claim(s) have no current support.")
    if overdue_withdrawal_reviews:
        why.append(
            f"{overdue_withdrawal_reviews} emergency withdrawal(s) have no retrospective review past their due time."
        )
    if unacknowledged_reports:
        why.append(
            f"{unacknowledged_reports} vulnerability report(s) are past their first-response target."
        )
    return not why, why


def horizon(today: date, days: int = 30) -> date:
    return today + timedelta(days=days)
