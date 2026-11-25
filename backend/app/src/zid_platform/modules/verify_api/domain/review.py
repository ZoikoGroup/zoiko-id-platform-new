"""Case review and four-eyes control for a partner's verifications (ZID-CP-WF04 v2.0; CR-167). Pure rules, no I/O.

The invariants this module holds, all from the wireframe's design lock:
1. a review PROPOSAL is never final; a case ends only through the finality gate (`finality_problems`);
2. the first and second reviewer are never the same principal;
3. every change carries the case version the reviewer read and an unexpired lease they hold, so a stale screen can change nothing;
4. a legal / regulatory hold blocks every change and finality, and no ordinary reviewer can lift it;
5. a finished case is never edited: a correction is a new APPEAL case whose decision SUPERSEDES the old one;
6. every step is an append-only event, each carrying the digest of the one before it, so a gap or an edit shows;
7. sensitive evidence is redacted by default and seen only through a reveal that names a reason, lasts minutes and is counted;
8. an AI adviser can only advise: it can read a redacted snapshot, must cite evidence that exists, must abstain when the evidence is stale or held, and can never move a case.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

from zid_platform.modules.verify_api.domain.model import PartnerError

LEASE_TTL = timedelta(minutes=15)
REVEAL_TTL = timedelta(minutes=5)
MAX_REVEALS_PER_CASE = 10
SLA = timedelta(hours=72)
NOTE_MIN, NOTE_MAX = 3, 1000
ADVICE_LIMIT_PER_CASE = 5
ADVICE_SUMMARY_MAX = 600


class CaseState(StrEnum):
    QUEUED = "QUEUED"
    CLAIMED = "CLAIMED"
    INVESTIGATING = "INVESTIGATING"
    AWAITING_APPLICANT = "AWAITING_APPLICANT"
    ESCALATED = "ESCALATED"
    SPECIALIST_REVIEW = "SPECIALIST_REVIEW"
    AWAITING_SECOND_REVIEW = "AWAITING_SECOND_REVIEW"
    RETURNED = "RETURNED"
    APPROVED_FINAL = "APPROVED_FINAL"


S = CaseState
TRANSITIONS: dict[CaseState, frozenset[CaseState]] = {
    S.QUEUED: frozenset({S.CLAIMED}),
    S.CLAIMED: frozenset(
        {
            S.INVESTIGATING,
            S.QUEUED,
            S.AWAITING_SECOND_REVIEW,
            S.AWAITING_APPLICANT,
            S.ESCALATED,
            S.APPROVED_FINAL,
        }
    ),
    S.INVESTIGATING: frozenset(
        {S.QUEUED, S.AWAITING_SECOND_REVIEW, S.AWAITING_APPLICANT, S.ESCALATED, S.APPROVED_FINAL}
    ),
    S.AWAITING_APPLICANT: frozenset({S.QUEUED}),
    S.ESCALATED: frozenset({S.SPECIALIST_REVIEW}),
    S.SPECIALIST_REVIEW: frozenset(
        {S.QUEUED, S.AWAITING_SECOND_REVIEW, S.AWAITING_APPLICANT, S.APPROVED_FINAL}
    ),
    S.AWAITING_SECOND_REVIEW: frozenset({S.RETURNED, S.APPROVED_FINAL, S.ESCALATED}),
    S.RETURNED: frozenset({S.INVESTIGATING}),
    S.APPROVED_FINAL: frozenset(),
}
OPEN_STATES = frozenset(set(CaseState) - {S.APPROVED_FINAL})
CLAIMABLE = frozenset({S.QUEUED, S.RETURNED, S.ESCALATED})
WORKING = frozenset(
    {S.CLAIMED, S.INVESTIGATING, S.SPECIALIST_REVIEW}
)  # states in which the lease holder may investigate, propose, ask or escalate


class Outcome(StrEnum):
    VERIFIED = "verified"
    NOT_VERIFIED = "not_verified"
    INCONCLUSIVE = "inconclusive"
    REQUEST_INFORMATION = "request_information"


FINAL_OUTCOMES = (Outcome.VERIFIED, Outcome.NOT_VERIFIED, Outcome.INCONCLUSIVE)
FINDINGS = ("pass", "fail", "inconclusive")

# fixed reason codes a reviewer may give; a free-text reason is never an outcome's reason
REASON_CODES: dict[str, str] = {
    "CHECKS_CONFIRMED": "Every required check was confirmed.",
    "CHECK_FAILED": "A required check failed.",
    "EVIDENCE_INSUFFICIENT": "The evidence is not enough to decide.",
    "DOC_IMAGE_BLURRY": "The document photograph was not clear enough.",
    "FACE_UNCLEAR": "The face photograph was not clear enough.",
    "DOCUMENT_EXPIRED": "The document is out of date.",
    "APPLICANT_NO_RESPONSE": "The applicant did not provide what was asked.",
    "APPEAL_UPHELD": "The appeal was upheld on new evidence.",
    "APPEAL_REJECTED": "The appeal was not upheld.",
}
INFO_TEMPLATES: dict[str, str] = {
    "RETAKE_DOCUMENT": "A new, clear photograph of the document.",
    "RETAKE_FACE": "A new, clear photograph of the face.",
    "DIFFERENT_DOCUMENT": "A different, valid identity document.",
}

# Evidence classes. STANDARD is shown; SENSITIVE is redacted until revealed; RESTRICTED is never revealed to a customer reviewer.
STANDARD, SENSITIVE, RESTRICTED = "STANDARD", "SENSITIVE", "RESTRICTED"
REVEALABLE = frozenset({STANDARD, SENSITIVE})


def check_transition(current: CaseState, target: CaseState) -> CaseState:
    if target not in TRANSITIONS[current]:
        raise PartnerError(
            "CASE_STATE_INVALID",
            f"A case that is {current.value.lower().replace('_', ' ')} cannot become {target.value.lower().replace('_', ' ')}.",
        )
    return target


def check_note(note: str | None, *, required: bool = True) -> str:
    clean = " ".join((note or "").split())
    if len(clean) > NOTE_MAX:
        raise PartnerError("VALIDATION_FAILED", f"The note is longer than {NOTE_MAX} characters.")
    if required and len(clean) < NOTE_MIN:
        raise PartnerError("NOTE_REQUIRED", "Give a reason.")
    return clean


def check_reason_code(code: str | None) -> str:
    if code not in REASON_CODES:
        raise PartnerError(
            "VALIDATION_FAILED",
            "Choose one of the listed reason codes: " + ", ".join(REASON_CODES) + ".",
        )
    return str(code)


def check_outcome(value: str | None) -> Outcome:
    try:
        return Outcome(str(value))
    except ValueError as e:
        raise PartnerError(
            "VALIDATION_FAILED",
            "Choose verified, not_verified, inconclusive or request_information.",
        ) from e


# ------------------------------------------------------------------------------- versions, leases and who may act


def check_version(current: int, expected: int | None) -> None:
    """The reviewer must have read the case as it is now. A missing version is as wrong as an old one."""
    if expected is None or expected != current:
        raise PartnerError(
            "CASE_STALE_VERSION",
            "The case changed after you read it. Reload it; nothing was changed.",
        )


@dataclass(frozen=True, slots=True)
class Lease:
    lease_id: str
    reviewer: str
    expires_at: datetime
    released: bool = False


def lease_problem(lease: Lease | None, reviewer: str, now: datetime) -> str | None:
    """Why this person may not act under a lease, or None. One message for every way it can be wrong, so the screen only needs to say 'claim it again'."""
    if lease is None or lease.released or lease.expires_at <= now or lease.reviewer != reviewer:
        return "ASSIGNMENT_CONFLICT"
    return None


def require_lease(lease: Lease | None, reviewer: str, now: datetime) -> None:
    if lease_problem(lease, reviewer, now):
        raise PartnerError(
            "ASSIGNMENT_CONFLICT",
            "You do not hold the lease on this case, or it has expired. Claim it again.",
        )


def check_independent(first: str, second: str) -> None:
    if first == second:
        raise PartnerError(
            "REVIEWER_NOT_INDEPENDENT",
            "A different reviewer from the one who proposed the outcome must give the second review.",
        )


# ------------------------------------------------------------------------------- evidence


def manifest_hash(refs: Iterable[Mapping[str, Any]]) -> str:
    """The digest of exactly what the case's evidence was, in one fixed spelling. A proposal names it; if the evidence changed since, the proposal can no longer be finalized."""
    rows = sorted(
        ({"id": r["evidence_id"], "kind": r["kind"], "hash": r["content_hash"]} for r in refs),
        key=lambda r: r["id"],
    )
    return (
        "sha256:"
        + hashlib.sha256(
            json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    )


def content_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def redact(ref: Mapping[str, Any]) -> dict[str, Any]:
    """What a list or a case view shows of one piece of evidence: what it is, its class and its fingerprint. The detail only through a reveal."""
    return {
        "evidence_id": ref["evidence_id"],
        "kind": ref["kind"],
        "label": ref["label"],
        "classification": ref["classification"],
        "content_hash": ref["content_hash"],
        "summary": ref["summary"] if ref["classification"] == STANDARD else None,
    }


def check_reveal(ref: Mapping[str, Any] | None, *, reason: str, reveals_so_far: int) -> str:
    if ref is None:
        raise PartnerError("NOT_FOUND", "No such evidence on this case.")
    if ref["classification"] not in REVEALABLE:
        raise PartnerError("EVIDENCE_RESTRICTED", "That evidence is not available to a reviewer.")
    if reveals_so_far >= MAX_REVEALS_PER_CASE:
        raise PartnerError("RATE_LIMITED", "This case has reached its limit of evidence reveals.")
    return check_note(reason)


# ------------------------------------------------------------------------------- findings, proposals and the finality gate


def check_findings(findings: Mapping[str, str], required: Sequence[str]) -> dict[str, str]:
    """A reviewer's finding on each required check. Only the checks the workflow requires, only pass, fail or inconclusive."""
    out: dict[str, str] = {}
    for method, value in findings.items():
        if method not in required:
            raise PartnerError("VALIDATION_FAILED", f"{method} is not a check this case requires.")
        if value not in FINDINGS:
            raise PartnerError("VALIDATION_FAILED", "A finding is pass, fail or inconclusive.")
        out[method] = value
    return out


def proposal_problems(
    outcome: Outcome, *, required: Sequence[str], findings: Mapping[str, str]
) -> list[str]:
    """Hard policy gate on a first-review outcome. There is no incomplete or unconfirmed route to `verified`."""
    if outcome is Outcome.VERIFIED:
        missing = [m for m in required if findings.get(m) != "pass"]
        return [f"MANDATORY_CHECK_MISSING:{m}" for m in missing]
    if outcome is Outcome.NOT_VERIFIED:
        return (
            [] if any(findings.get(m) == "fail" for m in required) else ["NO_FAILED_CHECK_RECORDED"]
        )
    return []


def finality_problems(
    *,
    case_state: CaseState,
    case_version: int,
    proposal_case_version: int,
    proposal_manifest: str,
    current_manifest: str,
    on_hold: bool,
    four_eyes_required: bool,
    first_reviewer: str,
    approver: str | None,
    proposal_outcome: Outcome,
    required: Sequence[str],
    findings: Mapping[str, str],
) -> list[str]:
    """Every reason the decision may not be finalized now. Empty means it may. The same checks run again inside the finality transaction, never trusted from an earlier screen."""
    out: list[str] = []
    if on_hold:
        out.append("REGULATORY_HOLD")
    if case_state is CaseState.APPROVED_FINAL:
        out.append("CASE_ALREADY_FINAL")
    if proposal_case_version > case_version:
        out.append("PROPOSAL_FROM_THE_FUTURE")
    if proposal_manifest != current_manifest:
        out.append("EVIDENCE_CHANGED")
    if proposal_outcome not in FINAL_OUTCOMES:
        out.append("NOT_A_FINAL_OUTCOME")
    out += proposal_problems(proposal_outcome, required=required, findings=findings)
    if four_eyes_required and (approver is None or approver == first_reviewer):
        out.append("SECOND_REVIEWER_REQUIRED")
    return out


# ------------------------------------------------------------------------------- the event chain


GENESIS = "0" * 64


def event_digest(
    prev: str,
    *,
    seq: int,
    case_id: str,
    event_type: str,
    actor: str,
    at: datetime,
    reason: str | None,
    data: Mapping[str, Any],
) -> str:
    body = json.dumps(
        {
            "prev": prev,
            "seq": seq,
            "case": case_id,
            "type": event_type,
            "actor": actor,
            "at": at.isoformat(),
            "reason": reason,
            "data": data,
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(body.encode()).hexdigest()


def chain_problems(events: Sequence[Mapping[str, Any]]) -> list[str]:
    """Where an event chain is broken: a missing step, a reordered one, or an edited one. Empty means the chain is whole."""
    out: list[str] = []
    prev = GENESIS
    for i, e in enumerate(events, start=1):
        if e["seq"] != i:
            out.append(f"GAP_AT_{i}")
        if e["prev_digest"] != prev:
            out.append(f"BROKEN_LINK_AT_{e['seq']}")
        want = event_digest(
            e["prev_digest"],
            seq=e["seq"],
            case_id=e["case_id"],
            event_type=e["event_type"],
            actor=e["actor"],
            at=e["created_at"],
            reason=e["reason"],
            data=e["data"],
        )
        if want != e["digest"]:
            out.append(f"EDITED_AT_{e['seq']}")
        prev = e["digest"]
    return out


# ------------------------------------------------------------------------------- the AI adviser (advisory only)

ADVICE_KEYS = frozenset({"summary", "suggested_outcome", "citations", "abstain", "abstain_reason"})
SUGGESTIONS = frozenset(o.value for o in Outcome)


@dataclass(frozen=True, slots=True)
class AdviceCheck:
    ok: bool
    flags: tuple[str, ...]


def adviser_must_abstain(*, evidence: Collection[Mapping[str, Any]], on_hold: bool) -> str | None:
    """When the adviser is not even asked: no evidence, an unavailable source, or a legal hold. It abstains and says why."""
    if on_hold:
        return "REGULATORY_HOLD"
    if not evidence:
        return "NO_EVIDENCE"
    if any(
        e.get("kind") == "check" and e.get("summary", {}).get("result") == "unavailable"
        for e in evidence
    ):
        return "SOURCE_UNAVAILABLE"
    return None


def validate_advice(advice: Any, *, citable: Collection[str]) -> AdviceCheck:
    """An adviser's answer is accepted only if it has exactly the allowed shape, every citation resolves to evidence this reviewer may see, it never proposes anything but a listed suggestion, and it cites something
    whenever it suggests an outcome. Anything else is rejected whole and shown to nobody as advice."""
    flags: list[str] = []
    if not isinstance(advice, dict):
        return AdviceCheck(False, ("NOT_AN_OBJECT",))
    if set(advice) - ADVICE_KEYS:
        flags.append("UNKNOWN_FIELD")
    summary = advice.get("summary")
    if not isinstance(summary, str) or len(summary) > ADVICE_SUMMARY_MAX:
        flags.append("BAD_SUMMARY")
    citations = advice.get("citations", [])
    if not isinstance(citations, list) or not all(isinstance(c, str) for c in citations):
        flags.append("BAD_CITATIONS")
    else:
        allowed = set(citable)
        if any(c not in allowed for c in citations):
            flags.append("CITATION_DOES_NOT_RESOLVE")
    suggestion = advice.get("suggested_outcome")
    if suggestion is not None and suggestion not in SUGGESTIONS:
        flags.append("BAD_SUGGESTION")
    if advice.get("abstain") is True:
        if suggestion is not None:
            flags.append("ABSTAINED_BUT_SUGGESTED")
    elif suggestion is not None and not advice.get("citations"):
        flags.append("SUGGESTION_WITHOUT_CITATION")
    return AdviceCheck(not flags, tuple(flags))
