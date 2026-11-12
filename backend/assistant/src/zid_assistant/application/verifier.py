"""Server-owned verification of a model draft (ZI-CHAT-KB-002 section 06 gates G1..G6; ADR-04, INV-04).

The model's text is untrusted. Nothing it says about citations, claims, capabilities or links is believed: every reference must
resolve to evidence the SERVER supplied, every claim class on that evidence must be eligible, and the text itself is scanned
for links, markup, secrets, account assertions and unapproved availability or assurance language. Any failure rejects the draft.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from zid_assistant.application.snapshot import Knowledge
from zid_assistant.domain.content import ClaimKind
from zid_assistant.domain.eligibility import Context, Verdict, claim_eligible
from zid_assistant.domain.sensitive import scan
from zid_assistant.domain.verdicts import Reason
from zid_assistant.infrastructure.model.base import EvidenceItem

MAX_RAW = 6000
MAX_TEXT = 900

_FORBIDDEN_SHAPES = re.compile(
    r"https?://|\bwww\.|<[a-z/!][^>]*>|\]\(|```|`[^`]+`|[\w.+-]+@[\w-]+\.[\w.-]+|\+?\d[\d ()-]{8,}\d|^\s*(?:[#>*-]|\d+\.)\s",
    re.IGNORECASE | re.MULTILINE,
)
_ACCOUNT_ASSERT = re.compile(
    r"\byour (?:account|verification|identity|credential|session|consent|balance)\b"
    r"|\bi (?:have |'ve |can |will )?(?:verified|confirmed your|reset|unlocked|sent you|changed|updated your|checked your)\b",
    re.IGNORECASE,
)
_AVAILABILITY = re.compile(
    r"\bzoiko(?: id)?\b[^.?!]{0,40}\b(?:supports?|offers?|provides?|includes?|enables?|ships|has (?:released|launched)"
    r"|is (?:available|live|released|certified|compliant|ready|generally available)|are (?:available|live))\b"
    r"|\b(?:available|supported|live) (?:today|now|in production|globally|worldwide)\b|\bproduction[- ]ready\b|\bgenerally available\b",
    re.IGNORECASE,
)
# Descriptive framing: statements about the design, goals or concepts are not claims that something is released.
_FRAMING = re.compile(
    r"\b(?:architecture|architectural|designed|design|intended|planned|proposed|aims?|goal|concept|conceptual|approach|model|framework|roadmap)\b",
    re.IGNORECASE,
)
_ASSURANCE = re.compile(
    r"\b(?:soc ?2|iso ?27\d{3}|hipaa|fedramp|pci[- ]?dss|csa star|gdpr[- ]compliant|c5 attestation)\b"
    r"|\b(?:is|are|was|were|been|being)\s+(?:independently\s+)?(?:certified|compliant|audited|accredited|attested)\b"
    r"|\b(?:certified|compliant|audited|attested|accredited)\s+(?:by|under|to|with|against)\b"
    r"|\b(?:hold|holds|have|has|achieved|obtained|maintain|maintains|passed)\b[^.?!]{0,24}\b(?:certification|attestation|audit|accreditation)\b"
    r"|\bguarantee[sd]?\b|\bsla\b|\buptime\b|\b99\.\d+\s?%|\b\d+\s?%|\bfree trial\b|\bper (?:month|year|user|seat)\b|[$€£]\s?\d",
    re.IGNORECASE,
)
_NEGATING = re.compile(
    r"\b(?:can(?:no|')?t|can’t|cannot|unable to|not able to|do(?:es)? not|don'?t|doesn’t|don’t|whether|if|until|unless|without)\b",
    re.IGNORECASE,
)
_SENT = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True, slots=True)
class Verified:
    answer_text: str
    refs: tuple[EvidenceItem, ...]
    claim_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Rejected:
    reason: Reason
    detail: str  # a safe code, never model text


def _reject(reason: Reason, detail: str) -> Rejected:
    return Rejected(reason, detail)


def language_gate(text: str, approved_kinds: set[ClaimKind]) -> Rejected | None:
    """Availability, assurance and commercial WORDING needs an approved claim of that kind (INV-02). Hedged sentences
    ("I cannot confirm that Zoiko iD supports X") and descriptive framing ("the architecture includes ...") are not assertions."""
    for sentence in _SENT.split(text):
        if _NEGATING.search(sentence):
            continue
        if (
            _AVAILABILITY.search(sentence)
            and not _FRAMING.search(sentence)
            and ClaimKind.PRODUCT not in approved_kinds
        ):
            return _reject(Reason.CAPABILITY_NOT_RELEASED, "availability_language")
        if _ASSURANCE.search(sentence) and not (
            {ClaimKind.SENSITIVE, ClaimKind.COMMERCIAL} & approved_kinds
        ):
            return _reject(Reason.CLAIM_NOT_APPROVED, "assurance_language")
    return None


_WORDS = re.compile(r"[a-z]{5,}")
_COMMON = frozenset(
    [
        "which",
        "their",
        "there",
        "these",
        "those",
        "would",
        "could",
        "should",
        "about",
        "other",
        "where",
        "while",
        "being",
        "using",
        "because",
        "within",
        "without",
        "between",
        "through",
        "often",
        "usually",
        "commonly",
        "typically",
        "example",
        "general",
        "simple",
        "basic",
        "please",
        "cannot",
        "confirm",
        "published",
        "information",
        "current",
        "currently",
        "zoiko",
    ]
)
MIN_GROUNDED = 0.55


def _stems(text: str) -> set[str]:
    return {w[:5] for w in _WORDS.findall(text.lower().replace("‑", "-")) if w not in _COMMON}


def _grounded_share(text: str, used: list[EvidenceItem]) -> float:
    """Share of the answer's distinctive words (by 5-letter stem) that appear in the passages it cites (G6). A semantic check this is
    not: it catches an answer that wandered off the evidence, for example a definition of a term the passages never mention."""
    mine = _stems(text)
    if not mine:
        return 1.0
    theirs: set[str] = set()
    for e in used:
        theirs |= _stems(e.text)
    return len(mine & theirs) / len(mine)


def verify(
    raw: str, evidence: tuple[EvidenceItem, ...], k: Knowledge, ctx: Context
) -> Verified | Rejected:
    if len(raw) > MAX_RAW:
        return _reject(Reason.MODEL_OUTPUT_REJECTED, "too_long")
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return _reject(Reason.MODEL_OUTPUT_REJECTED, "not_json")
    if not isinstance(data, dict) or set(data) != {"decision", "answer_text", "span_refs"}:
        return _reject(Reason.MODEL_OUTPUT_REJECTED, "schema")
    decision, text, refs = data["decision"], data["answer_text"], data["span_refs"]
    if decision == "ABSTAIN":
        return _reject(Reason.NO_SOURCE, "model_abstained")
    if decision != "ANSWER" or not isinstance(text, str) or not isinstance(refs, list):
        return _reject(Reason.MODEL_OUTPUT_REJECTED, "schema")
    text = text.strip()
    if not (1 <= len(text) <= MAX_TEXT):
        return _reject(Reason.MODEL_OUTPUT_REJECTED, "length")

    by_ref = {e.ref: e for e in evidence}
    used: list[EvidenceItem] = []
    for r in refs:
        if not isinstance(r, str) or r not in by_ref:
            return _reject(
                Reason.MODEL_OUTPUT_REJECTED, "unknown_citation"
            )  # G1: a minted citation is never believed
        if by_ref[r] not in used:
            used.append(by_ref[r])
    if not used:
        return _reject(
            Reason.NO_SOURCE, "no_support"
        )  # G2: an answer without evidence is not an answer

    if _FORBIDDEN_SHAPES.search(text):
        return _reject(
            Reason.MODEL_OUTPUT_REJECTED, "markup_or_link"
        )  # G4: links come only from server link ids
    if scan(text).blocked:
        return _reject(Reason.MODEL_OUTPUT_REJECTED, "sensitive_output")  # G5
    if _ACCOUNT_ASSERT.search(text):
        return _reject(Reason.MODEL_OUTPUT_REJECTED, "account_assertion")

    snap = k.snapshot
    claim_ids: list[str] = []
    cap_ids: list[str] = []
    for e in used:
        src = snap.sources.get(e.source_id)
        if src is None:
            return _reject(Reason.NO_SOURCE, "source_missing")
        for c in src.claim_ids:
            if c.lstrip("!") not in claim_ids:
                claim_ids.append(c.lstrip("!"))
        cap_ids += [c for c in src.eligible_capability_ids if c not in cap_ids]

    approved_kinds: set[ClaimKind] = set()
    for cid in claim_ids:
        res = claim_eligible(snap, cid, ctx, capability_ids=tuple(cap_ids))
        cls = snap.claims.get(cid)
        if res.verdict is Verdict.DENY_SECURE_ROUTE:
            return _reject(Reason.SECURE_ROUTE_ONLY, "secure_route_claim")
        if res.allowed and cls is not None:
            approved_kinds.add(cls.kind)
        # A source's claim classes label what it CAN support. An ineligible PRODUCT/SENSITIVE/COMMERCIAL class does not make a
        # generic explanation wrong: it only means the text may not use that kind of wording, which the gate below enforces. (G3)

    bad = language_gate(text, approved_kinds)
    if bad is not None:
        return bad

    if _grounded_share(text, used) < MIN_GROUNDED:
        return _reject(
            Reason.NO_SOURCE, "ungrounded"
        )  # G6: well-formed, but not what the cited passages say

    return Verified(text, tuple(used), tuple(claim_ids))
