"""Deterministic claim eligibility (ZI-CHAT-CSR-008 section 11; ADR-03, ADR-04, INV-02).

Pure functions over a compiled `Snapshot`. The model has no say here: a DENY or ABSTAIN verdict is final. Every failure mode,
including a missing snapshot, resolves to ABSTAIN (fail closed). Four independent gates must all pass for an affirmative
product claim: release (P01), approved source (P02), sensitive-claim approval (P04) and commercial fact (P05).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from .content import (
    Capability,
    CapabilityStatus,
    ClaimClass,
    ClaimKind,
    CommercialFact,
    ContentState,
    Destination,
    LinkState,
    Source,
)
from .verdicts import Reason


@dataclass(frozen=True, slots=True)
class Snapshot:
    """The compiled, immutable publication state. Built by `application.snapshot`; never mutated."""

    snapshot_id: str
    digest: str
    sample: bool
    signed: bool
    capabilities: dict[str, Capability]
    sources: dict[str, Source]
    destinations: dict[str, Destination]
    claims: dict[str, ClaimClass]
    facts: dict[str, CommercialFact]
    withdrawn_sources: frozenset[str] = (
        frozenset()
    )  # revocation watermark: checked at serving time (ADR-11)
    withdrawn_links: frozenset[str] = frozenset()
    withdrawn_capabilities: frozenset[str] = frozenset()


class Verdict(StrEnum):
    ALLOW = "ALLOW"
    ABSTAIN = "ABSTAIN"
    DENY_SECURE_ROUTE = "DENY_SECURE_ROUTE"


@dataclass(frozen=True, slots=True)
class Result:
    verdict: Verdict
    reason: Reason = Reason.OK

    @property
    def allowed(self) -> bool:
        return self.verdict is Verdict.ALLOW


@dataclass(frozen=True, slots=True)
class Context:
    market: str | None = None  # visitor-selected only; never inferred (INV-06)
    audience: str | None = None
    locale: str = "en-US"
    today: date | None = None
    allow_sample: bool = False  # local/test engineering samples may be served, labelled as samples


ALLOW = Result(Verdict.ALLOW)


def _abstain(reason: Reason) -> Result:
    return Result(Verdict.ABSTAIN, reason)


def capability_released(snapshot: Snapshot, capability_id: str, ctx: Context) -> Result:
    cap = snapshot.capabilities.get(capability_id)
    if cap is None or capability_id in snapshot.withdrawn_capabilities:
        return _abstain(Reason.CAPABILITY_NOT_RELEASED)
    if cap.status not in (CapabilityStatus.RELEASED,) or cap.public_eligibility != "PUBLIC":
        return _abstain(Reason.CAPABILITY_NOT_RELEASED)
    today = ctx.today or date.today()
    if cap.effective_from and cap.effective_from > today:
        return _abstain(Reason.CAPABILITY_NOT_RELEASED)
    if cap.expires_at and cap.expires_at < today:
        return _abstain(Reason.CAPABILITY_NOT_RELEASED)
    if not cap.market_codes or (ctx.market is None or ctx.market not in cap.market_codes):
        return _abstain(Reason.CAPABILITY_NOT_RELEASED)  # unknown or unmatched market: never guess
    if cap.audience_codes and (ctx.audience is None or ctx.audience not in cap.audience_codes):
        return _abstain(Reason.CAPABILITY_NOT_RELEASED)
    if not cap.test_evidence_refs or not cap.approver:
        return _abstain(Reason.CAPABILITY_NOT_RELEASED)  # released without evidence is not released
    return ALLOW


def source_eligible(snapshot: Snapshot, source_id: str, ctx: Context) -> Result:
    src = snapshot.sources.get(source_id)
    if src is None or source_id in snapshot.withdrawn_sources:
        return _abstain(
            Reason.SOURCE_WITHDRAWN if source_id in snapshot.withdrawn_sources else Reason.NO_SOURCE
        )
    if src.locale != ctx.locale:
        return _abstain(Reason.NO_SOURCE)
    published = src.state is ContentState.PUBLISHED
    sample_ok = (
        ctx.allow_sample and snapshot.sample and src.state is ContentState.DRAFT_NOT_PUBLISHED
    )
    if not (published or sample_ok):
        return _abstain(Reason.NO_SOURCE)
    if published and src.review_due_at and src.review_due_at < (ctx.today or date.today()):
        return _abstain(Reason.NO_SOURCE)  # expired review: cannot support a new affirmative answer
    return ALLOW


def link_usable(snapshot: Snapshot, link_id: str) -> Result:
    dest = snapshot.destinations.get(link_id)
    if dest is None or link_id in snapshot.withdrawn_links or dest.state is not LinkState.ACTIVE:
        return _abstain(Reason.NO_SOURCE)
    return ALLOW


def claim_eligible(
    snapshot: Snapshot,
    claim_id: str,
    ctx: Context,
    *,
    capability_ids: tuple[str, ...] = (),
    fact_ids: tuple[str, ...] = (),
) -> Result:
    """Is one claim class allowed to be asserted for this audience, market and time?"""
    claim = snapshot.claims.get(claim_id)
    if claim is None:
        return _abstain(Reason.CLAIM_NOT_APPROVED)
    if claim.kind is ClaimKind.SECURE_ROUTE:
        return Result(Verdict.DENY_SECURE_ROUTE, Reason.SECURE_ROUTE_ONLY)
    if claim.kind is ClaimKind.CONCEPT:
        return ALLOW
    if claim.kind is ClaimKind.PRODUCT:
        if not capability_ids:
            return _abstain(Reason.CAPABILITY_NOT_RELEASED)
        for cid in capability_ids:
            r = capability_released(snapshot, cid, ctx)
            if not r.allowed:
                return r
        return ALLOW
    if claim.kind is ClaimKind.SENSITIVE:
        return ALLOW if claim.approved and claim.approvers else _abstain(Reason.CLAIM_NOT_APPROVED)
    if claim.kind is ClaimKind.COMMERCIAL:
        for fid in fact_ids:
            fact = snapshot.facts.get(fid)
            if fact is None or fact.state is not ContentState.PUBLISHED or not fact.approver:
                return _abstain(Reason.CLAIM_NOT_APPROVED)
            if not fact.market_codes or ctx.market not in fact.market_codes:
                return _abstain(Reason.CLAIM_NOT_APPROVED)
        return ALLOW if fact_ids else _abstain(Reason.CLAIM_NOT_APPROVED)
    return _abstain(Reason.CLAIM_NOT_APPROVED)  # pragma: no cover - exhaustive above


def conflicting(snapshot: Snapshot, source_ids: tuple[str, ...]) -> bool:
    """Conflict quarantine: two sources that carry contradictory claim sets are not served together (CSR-008 section 11).

    A source can declare a `conflict:` marker in its claim ids (`!CL-xx`), meaning it contradicts that class; if another
    selected source asserts the class, the pair conflicts."""
    asserts: set[str] = set()
    denies: set[str] = set()
    for sid in source_ids:
        src = snapshot.sources.get(sid)
        if src is None:
            continue
        for c in src.claim_ids:
            (denies if c.startswith("!") else asserts).add(c.lstrip("!"))
    return bool(asserts & denies)
