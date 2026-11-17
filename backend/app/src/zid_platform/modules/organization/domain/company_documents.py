"""What company documents prove, and how old they may be (ZID-DTV-WF-02 K03, section 5; acceptance T08, T09, T25 age rules). Pure domain, no I/O.

There is no single age limit for every document. A certificate of incorporation proves an event that happened once; it proves nothing about whether the company is active
today. A registry extract speaks to current status and goes stale. A mandate is valid between its own dates and until it is revoked. So each document class proves specific
claims, and each claim has its own freshness rule that the market's signed policy sets. Nothing here rejects an old incorporation certificate for being old, and nothing
lets one stand in for a current-status proof.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum


class Claim(StrEnum):
    LEGAL_EXISTENCE_HISTORICAL = "LEGAL_EXISTENCE_HISTORICAL"  # the entity was formed (an event)
    CURRENT_STATUS = "CURRENT_STATUS"  # the entity is active now (needs a recent source)
    OWNERSHIP_STRUCTURE = "OWNERSHIP_STRUCTURE"
    REPRESENTATIVE_AUTHORITY = "REPRESENTATIVE_AUTHORITY"


@dataclass(frozen=True, slots=True)
class DocumentRule:
    """A market's rule for one document class: which claim it proves, and for how long that proof counts (None: the proof is an event and does not age)."""

    doc_class: str
    claim: Claim
    max_age: timedelta | None


@dataclass(frozen=True, slots=True)
class CompanyDocument:
    """`status_date` is the date the document speaks for (an extract's as-of date), not the upload date; `issued_on` is when it was made."""

    doc_id: str
    doc_class: str
    issued_on: datetime
    status_date: datetime | None = None
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    revoked: bool = False
    quarantined: bool = False  # failed a content check: it proves nothing and is never shown to a reviewer's preview


@dataclass(frozen=True, slots=True)
class Finding:
    claim: Claim
    satisfied: bool
    by: str | None  # the document that satisfies it
    reason: str | None  # stable code when it does not


def _speaks_for(doc: CompanyDocument, rule: DocumentRule, now: datetime) -> str | None:
    """None when the document counts for the claim now; otherwise why not."""
    if doc.quarantined:
        return "DOCUMENT_QUARANTINED"
    if doc.revoked:
        return "DOCUMENT_REVOKED"
    if doc.valid_from is not None and now < doc.valid_from:
        return "NOT_YET_VALID"
    if doc.valid_to is not None and now >= doc.valid_to:
        return "DOCUMENT_EXPIRED"
    if rule.max_age is not None:
        as_of = doc.status_date or doc.issued_on
        if now - as_of > rule.max_age:
            return "EVIDENCE_TOO_OLD"
    return None


def assess(
    required: Iterable[Claim],
    rules: Iterable[DocumentRule],
    documents: Iterable[CompanyDocument],
    *,
    now: datetime,
) -> list[Finding]:
    """For each claim the market requires, whether a document that proves it counts now. An older document that proves a different claim never fills in for it."""
    by_class: dict[str, list[DocumentRule]] = {}
    for r in rules:
        by_class.setdefault(r.doc_class, []).append(r)
    docs = list(documents)
    out: list[Finding] = []
    for claim in required:
        best_reason = "NO_DOCUMENT_FOR_CLAIM"
        winner: str | None = None
        for doc in docs:
            for rule in by_class.get(doc.doc_class, ()):
                if rule.claim is not claim:
                    continue
                why = _speaks_for(doc, rule, now)
                if why is None:
                    winner = doc.doc_id
                    break
                best_reason = why if best_reason == "NO_DOCUMENT_FOR_CLAIM" else best_reason
            if winner:
                break
        out.append(Finding(claim, winner is not None, winner, None if winner else best_reason))
    return out


def next_requests(findings: Iterable[Finding]) -> list[str]:
    """What to ask the applicant for, in plain codes. An unsatisfied claim is a request for evidence, never a rejection."""
    wanted = {
        "NO_DOCUMENT_FOR_CLAIM": "UPLOAD_DOCUMENT",
        "EVIDENCE_TOO_OLD": "UPLOAD_MORE_RECENT_EVIDENCE",
        "DOCUMENT_EXPIRED": "UPLOAD_CURRENT_DOCUMENT",
        "DOCUMENT_REVOKED": "UPLOAD_CURRENT_DOCUMENT",
        "NOT_YET_VALID": "WAIT_OR_UPLOAD_DOCUMENT_IN_FORCE",
        "DOCUMENT_QUARANTINED": "RESUBMIT_FILE",
    }
    return [
        f"{wanted.get(f.reason or '', 'UPLOAD_DOCUMENT')}:{f.claim.value}"
        for f in findings
        if not f.satisfied
    ]
