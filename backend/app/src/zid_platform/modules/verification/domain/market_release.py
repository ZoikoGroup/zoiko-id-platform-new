"""Market release gates (ZID-RCG-003 RCG-G10, WF-08; section 11; CR-072). Pure domain, no I/O.

A market (country, product, purpose) is opened by a RELEASE: one immutable version that pins the exact approved policy packs and carries the
document's seven evidence gates, then four independent approvals of its hash. Rules, each with a test:
- every gate RG-01 to RG-07 needs a PASSED result with an evidence reference, recorded by someone holding that gate's role and never by the
  release's author; a later result for the same gate replaces the earlier one, and the history is kept;
- a release is frozen only when no gate is missing or failed and at least one approved pack is pinned (RG-01 is checked by the system as well as
  attested); freezing computes the release hash over the market, the pinned pack hashes and the evidence behind each gate;
- RG-08: legal, security/GRC, QA and product owner each sign the exact hash, each a different person, none of them the author and none of them
  someone who recorded evidence for this release, so the people who prove a gate never approve it;
- a release is RELEASEABLE only with all four approvals; only a RELEASEABLE release can be LAUNCHED; withdrawing closes the market at once;
- any change is a new DRAFT version with no gate results and no approvals: nothing carries over, because evidence is current or it is nothing;
- a market is open only while a LAUNCHED release exists AND still covers every approved pack in force: a pack published or retired after the
  release breaks it (a pack edited after approval invalidates the release, document T03).

Nothing here says a gate is true: a result is somebody's attestation with an evidence reference, never the evidence itself.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum
from typing import Any

ATTESTOR_ROLES = ("legal", "regulatory_ops", "security", "privacy", "qa", "vendor_mgmt")
RELEASE_APPROVER_ROLES = ("legal", "security_grc", "qa", "product_owner")
EVIDENCE_REF = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._:/#@+=-]{2,255}$"
)  # a reference (a document id, a ticket, a URL), never the evidence itself


@dataclass(frozen=True)
class Gate:
    gate_id: str
    title: str
    owner_role: str
    material: str  # what the signoff must point at (document section 11)


GATES: tuple[Gate, ...] = (
    Gate(
        "RG-01",
        "Market law",
        "legal",
        "legal citation, applicability memo and current effective dates for the market",
    ),
    Gate(
        "RG-02",
        "Supplier status",
        "regulatory_ops",
        "exact legal entity, service and scheme authorization with an official source snapshot",
    ),
    Gate(
        "RG-03",
        "Evidence integrity",
        "security",
        "signature and provenance pipeline, source evidence, trace replay and retention tests",
    ),
    Gate(
        "RG-04",
        "Privacy and security",
        "privacy",
        "DPIA where required, permissions, minimization, regional keys and deletion tests",
    ),
    Gate(
        "RG-05",
        "Quality and inclusion",
        "qa",
        "independent accuracy, representative fairness, alternatives, accessibility and redress tests",
    ),
    Gate(
        "RG-06",
        "Continuity",
        "security",
        "supplier outage, credential rotation, kill switch, failover and disaster recovery drills",
    ),
    Gate(
        "RG-07",
        "Contract assurance",
        "vendor_mgmt",
        "commercial terms, DPA, license validity, approved sub-processors, support and incident SLA",
    ),
)
GATE_BY_ID = {g.gate_id: g for g in GATES}


class GateStatus(StrEnum):
    PASSED = "PASSED"
    FAILED = "FAILED"


class ReleaseStatus(StrEnum):
    DRAFT = "DRAFT"
    FROZEN = "FROZEN"
    RELEASEABLE = "RELEASEABLE"
    LAUNCHED = "LAUNCHED"
    WITHDRAWN = "WITHDRAWN"


class ReleaseError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


@dataclass(frozen=True)
class MarketKey:
    country: str
    product: str
    purpose: str


@dataclass(frozen=True)
class PackRef:
    pack_id: str
    version: int
    pack_hash: str


@dataclass(frozen=True)
class GateResult:
    gate: str
    status: GateStatus
    evidence_ref: str
    role: str
    recorded_by: str
    recorded_at: datetime


@dataclass(frozen=True)
class ReleaseApproval:
    approver: str
    release_hash: str


@dataclass(frozen=True)
class MarketRelease:
    release_id: str
    version: int
    market: MarketKey
    status: ReleaseStatus
    author: str
    pack_refs: tuple[PackRef, ...] = ()
    results: tuple[GateResult, ...] = ()  # the whole history, oldest first
    approvals: Mapping[str, ReleaseApproval] = field(default_factory=dict)
    release_hash: str | None = None


@dataclass(frozen=True)
class MarketDecision:
    open: bool
    reason: str
    release: MarketRelease | None = None


def new_release(release_id: str, market: MarketKey, *, author: str) -> MarketRelease:
    return MarketRelease(release_id, 1, market, ReleaseStatus.DRAFT, author)


def current_results(release: MarketRelease) -> dict[str, GateResult]:
    """The latest result for each gate; an earlier one is superseded, never erased."""
    out: dict[str, GateResult] = {}
    for r in release.results:
        out[r.gate] = r
    return out


def gate_blockers(release: MarketRelease) -> list[str]:
    """Why this release cannot be frozen yet; empty means every gate passed."""
    now = current_results(release)
    problems: list[str] = []
    for g in GATES:
        got = now.get(g.gate_id)
        if got is None:
            problems.append(f"RELEASE_GATE_MISSING:{g.gate_id}")
        elif got.status is not GateStatus.PASSED:
            problems.append(f"RELEASE_GATE_FAILED:{g.gate_id}")
    return problems


def compute_hash(release: MarketRelease, pack_refs: Iterable[PackRef] | None = None) -> str:
    refs = sorted(
        release.pack_refs if pack_refs is None else tuple(pack_refs),
        key=lambda p: (p.pack_id, p.version),
    )
    body: dict[str, Any] = {
        "release_id": release.release_id,
        "version": release.version,
        "market": [release.market.country, release.market.product, release.market.purpose],
        "packs": [[p.pack_id, p.version, p.pack_hash] for p in refs],
        "gates": [
            [g, r.evidence_ref, r.role, r.recorded_by]
            for g, r in sorted(current_results(release).items())
            if r.status is GateStatus.PASSED
        ],
    }
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def record_gate(
    release: MarketRelease,
    *,
    gate: str,
    status: GateStatus,
    evidence_ref: str,
    role: str,
    by: str,
    at: datetime,
) -> MarketRelease:
    if release.status is not ReleaseStatus.DRAFT:
        raise ReleaseError("RELEASE_NOT_DRAFT", release.status.value)
    spec = GATE_BY_ID.get(gate)
    if spec is None:
        raise ReleaseError("RELEASE_GATE_UNKNOWN", gate)
    if role != spec.owner_role:
        raise ReleaseError(
            "RELEASE_ROLE_MISMATCH", f"{gate} is attested by the {spec.owner_role} role"
        )
    if by == release.author:
        raise ReleaseError(
            "RELEASE_RECORDER_IS_AUTHOR", "the author of a release records no evidence for it"
        )
    if not EVIDENCE_REF.match(evidence_ref):
        raise ReleaseError(
            "RELEASE_EVIDENCE_REQUIRED", "a gate result points at its evidence by reference"
        )
    return replace(
        release, results=(*release.results, GateResult(gate, status, evidence_ref, role, by, at))
    )


def freeze(release: MarketRelease, pack_refs: Iterable[PackRef]) -> MarketRelease:
    if release.status is not ReleaseStatus.DRAFT:
        raise ReleaseError("RELEASE_NOT_DRAFT", release.status.value)
    refs = tuple(pack_refs)
    problems = gate_blockers(release)
    if not refs:
        problems.insert(
            0, "RELEASE_NO_APPROVED_PACK"
        )  # RG-01 is also checked by the system: no approved pack in force, nothing to release
    if problems:
        raise ReleaseError("RELEASE_NOT_FREEZABLE", ", ".join(problems))
    pinned = replace(release, pack_refs=tuple(sorted(refs, key=lambda p: (p.pack_id, p.version))))
    return replace(pinned, status=ReleaseStatus.FROZEN, release_hash=compute_hash(pinned))


def approve_release(
    release: MarketRelease, *, role: str, approver: str, signed_hash: str
) -> MarketRelease:
    if role not in RELEASE_APPROVER_ROLES:
        raise ReleaseError("RELEASE_APPROVER_ROLE_UNKNOWN", role)
    if release.status is not ReleaseStatus.FROZEN:
        raise ReleaseError("RELEASE_NOT_FROZEN", release.status.value)
    if approver == release.author:
        raise ReleaseError(
            "RELEASE_APPROVER_IS_AUTHOR", "the author of a release cannot approve it"
        )
    if approver in {r.recorded_by for r in release.results}:
        raise ReleaseError(
            "RELEASE_APPROVER_NOT_INDEPENDENT",
            "someone who recorded evidence for this release cannot approve it",
        )
    if any(a.approver == approver for r, a in release.approvals.items() if r != role):
        raise ReleaseError("RELEASE_APPROVER_NOT_DISTINCT", "one person cannot hold two approvals")
    if signed_hash != release.release_hash:
        raise ReleaseError(
            "RELEASE_APPROVAL_HASH_MISMATCH", "the approval signs a different release"
        )
    approvals = {**release.approvals, role: ReleaseApproval(approver, signed_hash)}
    done = all(r in approvals for r in RELEASE_APPROVER_ROLES)
    return replace(
        release,
        approvals=approvals,
        status=ReleaseStatus.RELEASEABLE if done else ReleaseStatus.FROZEN,
    )


def launch(release: MarketRelease) -> MarketRelease:
    if release.status is not ReleaseStatus.RELEASEABLE:
        raise ReleaseError("RELEASE_NOT_RELEASEABLE", release.status.value)
    return replace(release, status=ReleaseStatus.LAUNCHED)


def withdraw(release: MarketRelease) -> MarketRelease:
    if release.status not in (ReleaseStatus.RELEASEABLE, ReleaseStatus.LAUNCHED):
        raise ReleaseError("RELEASE_NOT_WITHDRAWABLE", release.status.value)
    return replace(release, status=ReleaseStatus.WITHDRAWN)


def revise(release: MarketRelease, *, author: str) -> MarketRelease:
    """A change is a new DRAFT version: no gate results and no approvals carry over."""
    if release.status is ReleaseStatus.DRAFT:
        raise ReleaseError("RELEASE_DRAFT_OPEN", "this version is still a draft")
    return MarketRelease(
        release.release_id, release.version + 1, release.market, ReleaseStatus.DRAFT, author
    )


def _covers(release: MarketRelease, packs_in_force: Iterable[PackRef]) -> bool:
    pinned = {(p.pack_id, p.version, p.pack_hash) for p in release.pack_refs}
    return {(p.pack_id, p.version, p.pack_hash) for p in packs_in_force} <= pinned


def market_decision(
    releases: Iterable[MarketRelease], market: MarketKey, packs_in_force: Iterable[PackRef]
) -> MarketDecision:
    """Whether the market is open now. Fail closed: no release, one not launched, a withdrawn one, or a pack changed since the release all say no."""
    mine = [r for r in releases if r.market == market]
    if not mine:
        return MarketDecision(False, "no_release")
    launched = [r for r in mine if r.status is ReleaseStatus.LAUNCHED]
    if not launched:
        return MarketDecision(
            False,
            "withdrawn"
            if any(r.status is ReleaseStatus.WITHDRAWN for r in mine)
            else "not_launched",
        )
    best = max(launched, key=lambda r: r.version)
    if not _covers(best, packs_in_force):
        return MarketDecision(False, "pack_changed_after_release", best)
    return MarketDecision(True, "release_launched", best)
