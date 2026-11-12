"""Jurisdiction and profession policy packs (ZID-RCG-003 RCG-G01, WF-02). Pure domain, no I/O.

A pack is the rules for one market: country, optional subdivision, optional profession, product and purpose. Rules:
- there is no generic global pass: a market no approved pack names is UNAVAILABLE, and a market with too little
  precision (unknown state, missing profession) is REVIEW with `policy_missing`, never APPLICABLE (T01, T02);
- a pack whose only legal sources are proposals cannot be published, and a source counts only while it is in force (T04);
- publishing needs the four independent approvals (legal, GRC, security, business owner), each signing the exact pack
  hash; the author approves nothing, and one person cannot hold two approvals (T36);
- any change to the rules makes a new DRAFT version with a new hash, so earlier approvals no longer apply (T03);
- a decision records the pack id, version and hash it used, so a later change never rewrites it (T37).

APPLICABLE means only "this pack governs the request". It is not a verification result and never a pass.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum
from typing import Any

from zid_platform.modules.verification.domain.countries import Availability

APPROVER_ROLES = ("legal", "grc", "security", "business_owner")


class PackStatus(StrEnum):
    DRAFT = "DRAFT"
    APPROVED = "APPROVED"
    RETIRED = "RETIRED"


class SourceStatus(StrEnum):
    LAW = "LAW"
    GUIDANCE = "GUIDANCE"
    PROPOSAL = "PROPOSAL"


class Outcome(StrEnum):
    APPLICABLE = "APPLICABLE"
    UNAVAILABLE = "UNAVAILABLE"
    REVIEW = "REVIEW"


class PackError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


@dataclass(frozen=True)
class LegalSource:
    instrument: str
    url: str
    status: SourceStatus
    retrieved_at: datetime
    effective_from: datetime
    effective_to: datetime | None
    owner: str

    def in_force(self, at: datetime) -> bool:
        if self.status is SourceStatus.PROPOSAL:
            return False
        return self.effective_from <= at and (self.effective_to is None or at < self.effective_to)


@dataclass(frozen=True)
class PackKey:
    country: str
    product: str
    purpose: str
    subdivision: str | None = None
    profession: str | None = None


@dataclass(frozen=True)
class Rules:
    evidence_minimum: tuple[str, ...]
    alternate_routes: tuple[str, ...] = ()
    lawful_basis: str = ""
    validity_days: int | None = None
    escalation: str | None = None


@dataclass(frozen=True)
class Approval:
    approver: str
    pack_hash: str


@dataclass(frozen=True)
class PolicyPack:
    pack_id: str
    key: PackKey
    version: int
    status: PackStatus
    sources: tuple[LegalSource, ...]
    rules: Rules
    author: str
    approvals: Mapping[str, Approval] = field(default_factory=dict)  # role -> who signed which hash


@dataclass(frozen=True)
class Resolution:
    outcome: Outcome
    reason: str
    pack: PolicyPack | None = None


def _canonical(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, tuple | list):
        return [_canonical(v) for v in value]
    if hasattr(value, "__dataclass_fields__"):
        return {k: _canonical(getattr(value, k)) for k in sorted(value.__dataclass_fields__)}
    return value


def pack_hash(pack: PolicyPack) -> str:
    """The hash an approval signs: the market, version, legal sources and rules. Not the status, author or approvals."""
    body = {
        "pack_id": pack.pack_id,
        "key": _canonical(pack.key),
        "version": pack.version,
        "sources": _canonical(pack.sources),
        "rules": _canonical(pack.rules),
    }
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def revise(
    pack: PolicyPack,
    *,
    author: str,
    sources: tuple[LegalSource, ...] | None = None,
    rules: Rules | None = None,
) -> PolicyPack:
    """A change is always a new DRAFT version with no approvals; the approved version is left exactly as it was."""
    return replace(
        pack,
        version=pack.version + 1,
        status=PackStatus.DRAFT,
        author=author,
        sources=pack.sources if sources is None else sources,
        rules=pack.rules if rules is None else rules,
        approvals={},
    )


def approve(pack: PolicyPack, *, role: str, approver: str, signed_hash: str) -> PolicyPack:
    if role not in APPROVER_ROLES:
        raise PackError("PACK_APPROVER_ROLE_UNKNOWN", role)
    if pack.status is not PackStatus.DRAFT:
        raise PackError("PACK_NOT_DRAFT", pack.status.value)
    if approver == pack.author:
        raise PackError("PACK_APPROVER_IS_AUTHOR", "the author cannot approve their own pack")
    if any(a.approver == approver for r, a in pack.approvals.items() if r != role):
        raise PackError("PACK_APPROVER_NOT_DISTINCT", "one person cannot hold two approvals")
    if signed_hash != pack_hash(pack):
        raise PackError("PACK_APPROVAL_HASH_MISMATCH", "the approval signs a different version")
    return replace(pack, approvals={**pack.approvals, role: Approval(approver, signed_hash)})


def publish_problems(pack: PolicyPack, *, at: datetime) -> list[str]:
    """Everything that stops a pack from being published now; empty means it may be."""
    problems: list[str] = []
    if pack.status is not PackStatus.DRAFT:
        problems.append("PACK_NOT_DRAFT")
    if not any(s.in_force(at) for s in pack.sources):
        problems.append(
            "PACK_NO_SOURCE_IN_FORCE"
        )  # no source, only proposals, or none in force yet
    current = pack_hash(pack)
    for role in APPROVER_ROLES:
        got = pack.approvals.get(role)
        if got is None:
            problems.append(f"PACK_APPROVAL_MISSING:{role}")
        elif got.pack_hash != current:
            problems.append(f"PACK_APPROVAL_STALE:{role}")  # signed an earlier version (T03)
        elif got.approver == pack.author:
            problems.append(f"PACK_APPROVER_IS_AUTHOR:{role}")
    approvers = [a.approver for a in pack.approvals.values()]
    if len(approvers) != len(set(approvers)):
        problems.append("PACK_APPROVER_NOT_DISTINCT")
    return problems


def publish(pack: PolicyPack, *, at: datetime) -> PolicyPack:
    problems = publish_problems(pack, at=at)
    if problems:
        raise PackError("PACK_NOT_PUBLISHABLE", ", ".join(problems))
    return replace(pack, status=PackStatus.APPROVED)


def retire(pack: PolicyPack) -> PolicyPack:
    return replace(pack, status=PackStatus.RETIRED)


def resolve(
    packs: Iterable[PolicyPack],
    *,
    country: str,
    product: str,
    purpose: str,
    at: datetime,
    subdivision: str | None = None,
    profession: str | None = None,
) -> Resolution:
    """Which approved pack governs this request. Fail closed: no pack, no precision, no source in force: never APPLICABLE."""
    live = [
        p
        for p in packs
        if p.status is PackStatus.APPROVED
        and p.key.country == country.upper()
        and p.key.product == product
        and p.key.purpose == purpose
    ]
    if not live:
        return Resolution(Outcome.UNAVAILABLE, "market_not_approved")
    in_force = [p for p in live if any(s.in_force(at) for s in p.sources)]
    if not in_force:
        return Resolution(Outcome.UNAVAILABLE, "no_source_in_force")
    same_profession = [p for p in in_force if p.key.profession == profession]
    if not same_profession:
        return Resolution(
            Outcome.REVIEW, "policy_missing"
        )  # a profession pack exists but not this one (or none was given)
    exact = [
        p for p in same_profession if subdivision is not None and p.key.subdivision == subdivision
    ]
    wide = [p for p in same_profession if p.key.subdivision is None]
    chosen = exact or wide
    if not chosen:
        reason = "require_manual_jurisdiction" if subdivision is None else "policy_missing"
        return Resolution(Outcome.REVIEW, reason)
    best = max(chosen, key=lambda p: p.version)
    return Resolution(Outcome.APPLICABLE, "pack_in_force", best)


def decision_stamp(pack: PolicyPack) -> dict[str, str | int]:
    """What a decision stores so a later pack change never rewrites it (T37)."""
    return {"pack_id": pack.pack_id, "version": pack.version, "pack_hash": pack_hash(pack)}


def stamp_matches(pack: PolicyPack, stamp: Mapping[str, str | int]) -> bool:
    return decision_stamp(pack) == dict(stamp)


# ---- the gate: what a request is told once its country is known (ZID-RCG-003 T01, T02) -----------------------------------

GATE_MODES = ("off", "shadow", "enforce")
PACK_PURPOSE = "STANDARD"  # the purpose of the capabilities that exist today; a proposed value (CR-067), kept in one place


@dataclass(frozen=True)
class GateDecision:
    allowed: bool  # may the request go on now
    would_refuse: bool  # the pack check says no; in shadow mode the request still goes on
    code: str | None
    reason: str


def pack_needed(mode: str, availability: Availability) -> bool:
    """Whether the packs are consulted at all. The country registry stays the baseline: a capability that is not ENABLED there is decided by
    the country gate (DISABLED is refused, SANDBOX is a development stand-in that never counts outside local and test), so no pack is read."""
    if mode not in GATE_MODES:
        raise PackError("PACK_GATE_MODE_UNKNOWN", mode)
    return mode != "off" and availability is Availability.ENABLED


def gate_decision(
    mode: str, availability: Availability, resolution: Resolution | None
) -> GateDecision:
    """ENABLED in the country registry is not enough: an approved pack must also govern the request, or it is refused (enforce) or only
    reported (shadow). No pack, no precision and no source in force never turn into a pass; a missing resolution counts as no pack."""
    if not pack_needed(mode, availability):
        return GateDecision(True, False, None, "off" if mode == "off" else "country_gate_decides")
    if resolution is not None and resolution.outcome is Outcome.APPLICABLE:
        return GateDecision(True, False, None, resolution.reason)
    if resolution is not None and resolution.outcome is Outcome.REVIEW:
        code, reason = "REGULATORY_REVIEW_REQUIRED", resolution.reason
    else:
        code, reason = (
            "REGULATORY_POLICY_UNAVAILABLE",
            resolution.reason if resolution else "no_resolution",
        )
    return GateDecision(mode == "shadow", True, code, reason)
