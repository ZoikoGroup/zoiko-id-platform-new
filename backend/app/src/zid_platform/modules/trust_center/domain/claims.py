"""Claims governance (ZID-CP-WF12 v2.0, WF-12.09 and WF-12.11): a public sentence is an owned, typed, scoped, time-bound object, and nothing is published without an approval bound to its exact content. Pure domain, no I/O.

The rules, each pinned by a test:
- a claim's content hash covers its exact text, locale, scope, channels, evidence digests and expiry: change one character and every earlier approval stops counting;
- approvers are distinct people holding distinct required roles, and nobody approves their own claim;
- a publish re-checks everything at publish time (hash, evidence still current, approvals still current), so a stale approval cannot ship;
- an emergency withdrawal needs one authorized responder and no approval, because stopping harm must not wait; putting a claim back needs a new version that passes the normal gate;
- a rollback goes only to the latest version that was validly approved and never to a withdrawn one.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any


class ClaimType(StrEnum):
    NUMERICAL = "numerical"
    CERTIFICATION = "certification"
    THIRD_PARTY_ASSESSMENT = "third_party_assessment"
    LEGAL = "legal"
    CUSTOMER_ENDORSEMENT = "customer_endorsement"
    SLA = "sla"
    FUNCTIONALITY = "functionality"
    ROADMAP = "roadmap"


class Status(StrEnum):
    DRAFT = "DRAFT"
    EVIDENCE_MISSING = "EVIDENCE_MISSING"
    SCOPE_MISMATCH = "SCOPE_MISMATCH"
    APPROVAL_PENDING = "APPROVAL_PENDING"
    PUBLISHED = "PUBLISHED"
    EXPIRED = "EXPIRED"
    SUPERSEDED = "SUPERSEDED"
    EMERGENCY_WITHDRAWN = "EMERGENCY_WITHDRAWN"


class Role(StrEnum):
    PRODUCT = "product"
    LEGAL = "legal"
    SECURITY = "security"
    PRIVACY = "privacy"
    SRE = "sre"
    FINANCE = "finance"
    MARKETING = "marketing"
    RESPONDER = "responder"  # the emergency withdrawal role (Security or Legal incident responder)


# who must approve a claim of each class. Risk-driven: a number needs Finance and SRE-or-Product proof, a certification needs Security and Legal. Every class needs at least two distinct roles.
REQUIRED_ROLES: Mapping[ClaimType, frozenset[Role]] = {
    ClaimType.NUMERICAL: frozenset({Role.FINANCE, Role.PRODUCT}),
    ClaimType.CERTIFICATION: frozenset({Role.SECURITY, Role.LEGAL}),
    ClaimType.THIRD_PARTY_ASSESSMENT: frozenset({Role.SECURITY, Role.LEGAL}),
    ClaimType.LEGAL: frozenset({Role.LEGAL, Role.PRIVACY}),
    ClaimType.CUSTOMER_ENDORSEMENT: frozenset({Role.MARKETING, Role.LEGAL}),
    ClaimType.SLA: frozenset({Role.SRE, Role.LEGAL}),
    ClaimType.FUNCTIONALITY: frozenset({Role.PRODUCT, Role.SECURITY}),
    ClaimType.ROADMAP: frozenset({Role.PRODUCT, Role.LEGAL}),
}

# words that make a sentence a certification or compliance statement whatever its declared type; such a sentence is classified up, never down
CERT_WORDS = (
    "certified",
    "certification",
    "soc 2",
    "iso 27001",
    "fedramp",
    "hipaa",
    "compliant",
    "accredited",
    "qualified",
)


def classify(text: str, declared: ClaimType) -> ClaimType:
    """A sentence that sounds like a certification is treated as one, whatever the writer chose."""
    low = text.lower()
    if declared not in (ClaimType.CERTIFICATION, ClaimType.THIRD_PARTY_ASSESSMENT) and any(
        w in low for w in CERT_WORDS
    ):
        return ClaimType.CERTIFICATION
    return declared


@dataclass(frozen=True)
class Scope:
    entity_id: str
    service: str
    markets: tuple[str, ...]
    environment: str = "production"


@dataclass(frozen=True)
class Claim:
    claim_id: str
    revision: int
    exact_text: str
    locale: str
    claim_type: ClaimType
    scope: Scope
    channels: tuple[str, ...]
    evidence_digests: tuple[
        str, ...
    ]  # the digests of the evidence the writer relied on, bound into the hash
    valid_until: date | None
    author: str
    status: Status = Status.DRAFT


def content_hash(c: Claim) -> str:
    """The hash an approval is bound to. Sorted, canonical, and covering everything a reader could be misled by."""
    body = {
        "claim_id": c.claim_id, "revision": c.revision, "text": c.exact_text, "locale": c.locale, "type": str(c.claim_type),
        "scope": {"entity": c.scope.entity_id, "service": c.scope.service, "markets": sorted(c.scope.markets), "environment": c.scope.environment},
        "channels": sorted(c.channels), "evidence": sorted(c.evidence_digests), "valid_until": c.valid_until.isoformat() if c.valid_until else None,
    }  # fmt: skip
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


@dataclass(frozen=True)
class Approval:
    approver: str
    role: Role
    claim_hash: str
    evidence_digests: tuple[str, ...]
    decided_at: datetime
    effective_until: datetime | None = None
    refused: bool = False
    reason: str = ""


class ApprovalError(Exception):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def approve(
    c: Claim,
    *,
    approver: str,
    role: Role,
    existing: Sequence[Approval],
    evidence_digests: Sequence[str],
    now: datetime,
    effective_until: datetime | None = None,
) -> Approval:
    """One reviewer's approval of this exact version. Refused: the author approving their own claim, a role the class does not need, the same person twice, and evidence that differs from what the claim names."""
    if approver == c.author:
        raise ApprovalError("SELF_APPROVAL", "The author of a claim cannot approve it.")
    needed = REQUIRED_ROLES[classify(c.exact_text, c.claim_type)]
    if role not in needed:
        raise ApprovalError("ROLE_NOT_REQUIRED", f"A {role} approval is not one this claim needs.")
    h = content_hash(c)
    live = [a for a in existing if a.claim_hash == h and not a.refused]
    if any(a.approver == approver for a in live):
        raise ApprovalError("ALREADY_APPROVED", "That person has already approved this version.")
    if any(a.role == role for a in live):
        raise ApprovalError(
            "ROLE_ALREADY_COVERED",
            "That role has already approved this version; a second, different role is needed.",
        )
    if sorted(evidence_digests) != sorted(c.evidence_digests):
        raise ApprovalError(
            "EVIDENCE_DIFFERS", "The evidence reviewed is not the evidence this claim names."
        )
    return Approval(
        approver=approver,
        role=role,
        claim_hash=h,
        evidence_digests=tuple(sorted(evidence_digests)),
        decided_at=now,
        effective_until=effective_until,
    )


def refuse(c: Claim, *, approver: str, role: Role, reason: str, now: datetime) -> Approval:
    if approver == c.author:
        raise ApprovalError("SELF_APPROVAL", "The author of a claim cannot review it.")
    if not reason.strip():
        raise ApprovalError("REASON_REQUIRED", "A refusal says why.")
    return Approval(
        approver=approver,
        role=role,
        claim_hash=content_hash(c),
        evidence_digests=(),
        decided_at=now,
        refused=True,
        reason=reason,
    )


def standing(c: Claim, approvals: Sequence[Approval], *, now: datetime) -> tuple[Status, list[str]]:
    """Where this version stands, and what is still missing. An approval counts only for the exact hash, from a distinct person, still in date, with the evidence the claim names."""
    h = content_hash(c)
    needed = REQUIRED_ROLES[classify(c.exact_text, c.claim_type)]
    if not c.evidence_digests:
        return Status.EVIDENCE_MISSING, ["No evidence is linked to this claim."]
    if any(a.claim_hash == h and a.refused for a in approvals):
        return Status.DRAFT, ["A reviewer refused this version."]
    good = [
        a for a in approvals
        if a.claim_hash == h and not a.refused and sorted(a.evidence_digests) == sorted(c.evidence_digests) and (a.effective_until is None or a.effective_until > now) and a.approver != c.author
    ]  # fmt: skip
    covered = {a.role for a in good}
    if len({a.approver for a in good}) < 2:
        covered = set()  # one person cannot cover two roles: at least two distinct people are needed whatever the roles
    missing = sorted(str(r) for r in needed - covered)
    if missing:
        return Status.APPROVAL_PENDING, [f"Still needs approval from: {', '.join(missing)}."]
    return Status.APPROVAL_PENDING, []


@dataclass(frozen=True)
class EvidenceView:
    digest: str
    current: bool  # not expired, suspended or withdrawn, as of now
    scope_covers: bool  # the evidence's assessed scope covers this claim's scope
    verified: bool
    is_supplier: bool = False
    self_referential: bool = False  # a link to another marketing page


def publish_blockers(
    c: Claim,
    approvals: Sequence[Approval],
    evidence: Mapping[str, EvidenceView],
    *,
    expected_hash: str,
    now: datetime,
) -> list[tuple[str, str]]:
    """Everything that stops this claim being published right now, checked again at publish time. Empty means it may go. A tampered bundle (the hash differs from the one approved) is a blocker, not a warning."""
    out: list[tuple[str, str]] = []
    if content_hash(c) != expected_hash:
        out.append(("BUNDLE_TAMPERED", "The content differs from what was approved."))
    if c.valid_until is not None and c.valid_until < now.date():
        out.append(("CLAIM_EXPIRED", "The claim's own validity has passed."))
    if not c.evidence_digests:
        out.append(("EVIDENCE_MISSING", "No evidence is linked."))
    for d in c.evidence_digests:
        e = evidence.get(d)
        if e is None:
            out.append(("EVIDENCE_MISSING", f"Evidence {d[:12]} is not on record."))
        elif e.self_referential:
            out.append(("EVIDENCE_SELF_REFERENTIAL", "Evidence cannot be another marketing page."))
        elif not e.verified:
            out.append(("EVIDENCE_UNVERIFIED", f"Evidence {d[:12]} has no verified origin."))
        elif not e.current:
            out.append(("EVIDENCE_EXPIRED", f"Evidence {d[:12]} is no longer current."))
        elif not e.scope_covers:
            out.append(
                (
                    "SCOPE_MISMATCH",
                    f"Evidence {d[:12]} covers another entity, service, region or period.",
                )
            )
        elif (
            e.is_supplier
            and classify(c.exact_text, c.claim_type) == ClaimType.CERTIFICATION
            and not _says_supplier(c.exact_text)
        ):
            out.append(
                (
                    "SUPPLIER_EVIDENCE_AS_OWN",
                    "A supplier's certification cannot be shown as Zoiko iD's own.",
                )
            )
    status, missing = standing(c, approvals, now=now)
    if status != Status.APPROVAL_PENDING or missing:
        out.append(("APPROVAL_INCOMPLETE", missing[0] if missing else status.value))
    return out


def _says_supplier(text: str) -> bool:
    low = text.lower()
    return "supplier" in low or "provider" in low or "subprocessor" in low


@dataclass(frozen=True)
class Revision:
    revision: int
    claim_hash: str
    approved_validly: bool  # was published through the gate
    withdrawn: bool = False
    published_at: datetime | None = None


def rollback_target(history: Sequence[Revision], *, current: int) -> Revision | None:
    """The version a rollback may return to: the latest earlier one that went through the gate and was never withdrawn. Never an unapproved draft and never a withdrawn claim."""
    for r in sorted(history, key=lambda r: r.revision, reverse=True):
        if r.revision < current and r.approved_validly and not r.withdrawn:
            return r
    return None


class WithdrawError(Exception):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class Withdrawal:
    claim_id: str
    actor: str
    reason: str
    at: datetime
    channels: tuple[str, ...]
    review_due: datetime  # a retrospective review is demanded, not optional
    purge_required: bool = True  # CDN and cached copies


def emergency_withdraw(
    c: Claim,
    *,
    actor: str,
    actor_roles: Sequence[Role],
    reason: str,
    now: datetime,
    review_within_hours: int = 72,
) -> Withdrawal:
    """One responder is enough: stopping harm does not wait for approvers. Needs a reason and a retrospective review date; cannot create or approve anything."""
    if Role.RESPONDER not in actor_roles:
        raise WithdrawError(
            "NOT_A_RESPONDER", "Only an authorized incident responder can withdraw a claim."
        )
    if not reason.strip():
        raise WithdrawError("REASON_REQUIRED", "A withdrawal says why.")
    from datetime import timedelta

    return Withdrawal(
        claim_id=c.claim_id,
        actor=actor,
        reason=reason,
        at=now,
        channels=c.channels,
        review_due=now + timedelta(hours=review_within_hours),
    )


def after_withdrawal(withdrawn: Claim, candidate: Claim) -> list[str]:
    """Restoring a withdrawn claim: the same hash is refused. It must be a new revision with different evidence or text, then it goes through the normal gate."""
    if candidate.claim_id == withdrawn.claim_id and content_hash(candidate) == content_hash(
        withdrawn
    ):
        return [
            "The withdrawn claim cannot return unchanged; submit a new version with new evidence."
        ]
    return []


def expired_by(c: Claim, *, today: date | None = None) -> bool:
    d = today or datetime.now(UTC).date()
    return c.valid_until is not None and c.valid_until < d


@dataclass
class Projection:
    """The public read of one claim: only what a visitor may see. Never the approvals' reasons, the reviewers or the artifacts' storage."""

    claim_id: str
    text: str
    locale: str
    scope: dict[str, Any]
    effective_as_of: datetime
    valid_until: date | None
    historical: bool = False
    extra: dict[str, Any] = field(default_factory=dict)


def project(c: Claim, *, published: bool, as_of: datetime, today: date) -> Projection | None:
    """Public projection. A claim not published, or past its validity, is not shown as active; one past its date may show as explicitly historical by the caller, never as a live badge."""
    if not published or c.status in (
        Status.EMERGENCY_WITHDRAWN,
        Status.DRAFT,
        Status.EVIDENCE_MISSING,
        Status.SCOPE_MISMATCH,
        Status.APPROVAL_PENDING,
    ):
        return None
    if expired_by(c, today=today):
        return None
    return Projection(
        claim_id=c.claim_id, text=c.exact_text, locale=c.locale,
        scope={"entity_id": c.scope.entity_id, "service": c.scope.service, "markets": list(c.scope.markets), "environment": c.scope.environment},
        effective_as_of=as_of, valid_until=c.valid_until,
    )  # fmt: skip
