"""The screening decision (ZID-ED-04 sections 02, 04, 06). Pure domain, no I/O.

Four separate assertions are never merged: the entity exists (ED-03), the person is who they claim (ED-05), the person is
authorized to act for the entity (here), and the relationship is permitted under sanctions and risk policy (here).
Passing one never satisfies another. Rules:
- a list that is missing or stale means the party was not screened: UNAVAILABLE, never VERIFIED;
- any alert, a sanctions ownership finding, an unresolved ownership chain, a country with no pack, or authority nobody
  proved goes to a person (REVIEW_REQUIRED); no provider and no rule here approves or rejects anyone;
- REJECTED is a reviewer's ruling and cannot come from a rule;
- a decision names the exact list versions and policy that produced it, and the customer sees only a safe outcome.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

from zid_platform.modules.screening.domain.lists import ListId, Snapshot
from zid_platform.modules.screening.domain.matching import (
    Match,
    MatchPolicy,
    Party,
    screen_party,
)
from zid_platform.modules.screening.domain.ownership import (
    CountryPack,
    Graph,
    OwnershipResult,
    evaluate,
)


class ScreeningError(Exception):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


class ScreeningStatus(StrEnum):
    NOT_STARTED = "NOT_STARTED"
    GATHERING = "GATHERING"
    SCREENING = "SCREENING"
    VERIFIED = "VERIFIED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    REJECTED = "REJECTED"
    UNAVAILABLE = "UNAVAILABLE"
    EXPIRED = "EXPIRED"
    SUSPENDED = "SUSPENDED"


class Role(StrEnum):
    ENTITY = "ENTITY"
    REPRESENTATIVE = "REPRESENTATIVE"
    DIRECTOR = "DIRECTOR"
    OWNER = "OWNER"


class AuthorityKind(StrEnum):
    REGISTER_OFFICER = "REGISTER_OFFICER"  # a current director or officer on the official register
    DELEGATED_MANDATE = "DELEGATED_MANDATE"  # a lawful delegation with its own reference and expiry
    NONE = "NONE"


@dataclass(frozen=True, slots=True)
class AuthorityEvidence:
    kind: AuthorityKind
    reference: str | None = None  # an id of the register entry or the mandate, never a name
    expires_at: datetime | None = None

    def valid(self, now: datetime) -> bool:
        if self.kind is AuthorityKind.NONE or not self.reference:
            return False
        return self.expires_at is None or self.expires_at > now


@dataclass(frozen=True, slots=True)
class Policy:
    version: str
    required_lists: tuple[ListId, ...]
    list_max_age: timedelta  # a list older than this was not current when the party was screened
    validity: timedelta  # how long a clear outcome stands
    validity_elevated: timedelta  # for elevated risk: refreshed more often
    match: MatchPolicy = field(default_factory=MatchPolicy)


@dataclass(frozen=True, slots=True)
class Subject:
    role: Role
    party: Party


@dataclass(frozen=True, slots=True)
class ScreeningInput:
    country: str
    subjects: tuple[Subject, ...]
    graph: Graph | None
    authority: AuthorityEvidence
    elevated_risk: bool
    snapshots: Mapping[ListId, Snapshot]
    pack: CountryPack | None
    policy: Policy
    now: datetime


@dataclass(frozen=True, slots=True)
class Decision:
    status: ScreeningStatus
    reasons: tuple[str, ...]
    matches: tuple[Match, ...]
    ownership: OwnershipResult | None
    authority_verified: bool
    lists: tuple[tuple[str, str, str], ...]  # (list id, version, content hash) that were checked
    expiry_at: datetime | None


def _lists_state(inp: ScreeningInput) -> tuple[list[str], list[Snapshot]]:
    problems: list[str] = []
    usable: list[Snapshot] = []
    required = list(inp.policy.required_lists)
    if inp.pack is not None and inp.pack.local_list_required and ListId.LOCAL not in required:
        required.append(ListId.LOCAL)  # the country's own list, where its pack says it is needed
    for list_id in required:
        snap = inp.snapshots.get(list_id)
        if snap is None or snap.size == 0:
            problems.append(f"LIST_UNAVAILABLE:{list_id.value}")
        elif inp.now - snap.fetched_at > inp.policy.list_max_age:
            problems.append(f"LIST_STALE:{list_id.value}")
        else:
            usable.append(snap)
    return problems, usable


def decide(inp: ScreeningInput) -> Decision:
    problems, usable = _lists_state(inp)
    checked = tuple(sorted((s.list_id.value, s.version, s.content_hash) for s in usable))
    if problems:  # not screened: never clear (ED-04 section 08)
        return Decision(
            ScreeningStatus.UNAVAILABLE, tuple(problems), (), None, False, checked, None
        )

    roles = {s.role for s in inp.subjects}
    missing = [
        f"MISSING_PARTY:{r.value}" for r in (Role.ENTITY, Role.REPRESENTATIVE) if r not in roles
    ]
    if (
        missing
    ):  # entity and authorized representative are always required before a verified outcome
        return Decision(ScreeningStatus.GATHERING, tuple(missing), (), None, False, checked, None)

    needs_ownership = inp.pack is not None and bool(
        inp.pack.sanctions_evaluators or inp.pack.beneficial_owner_rule
    )
    if needs_ownership and inp.graph is None:
        return Decision(
            ScreeningStatus.GATHERING, ("MISSING_OWNERSHIP",), (), None, False, checked, None
        )

    matches: list[Match] = []
    for subject in inp.subjects:
        matches += screen_party(subject.party, usable, inp.policy.match)
    alerted = frozenset(m.party_ref for m in matches)

    reasons: list[str] = []
    ownership: OwnershipResult | None = None
    if inp.pack is None:
        reasons.append("COUNTRY_PACK_MISSING")  # no universal threshold is ever assumed
    elif inp.graph is not None:
        ownership = evaluate(inp.graph, inp.pack, designated=alerted)
        reasons += list(ownership.flags)
        reasons += [f"OWNERSHIP_{r}" for r in ownership.unresolved]
        owner_refs = {s.party.ref for s in inp.subjects if s.role is Role.OWNER}
        reasons += [f"OWNER_NOT_SCREENED:{r}" for r in ownership.identify if r not in owner_refs]
    if matches:
        reasons.append("SCREENING_ALERT")
        reasons += sorted({f"ALERT_{m.strength.value}" for m in matches})
    authority_ok = inp.authority.valid(inp.now)
    if not authority_ok:
        reasons.append(
            "AUTHORITY_UNPROVEN"
        )  # a registered company is not a person's authority to act for it
    if reasons:
        return Decision(
            ScreeningStatus.REVIEW_REQUIRED,
            tuple(dict.fromkeys(reasons)),
            tuple(matches),
            ownership,
            authority_ok,
            checked,
            None,
        )
    validity = inp.policy.validity_elevated if inp.elevated_risk else inp.policy.validity
    return Decision(
        ScreeningStatus.VERIFIED,
        ("SCREENED_CLEAR", "AUTHORITY_PROVEN"),
        (),
        ownership,
        True,
        checked,
        inp.now + validity,
    )


def effective_status(
    status: ScreeningStatus, expiry_at: datetime | None, now: datetime
) -> ScreeningStatus:
    if status is ScreeningStatus.VERIFIED and (expiry_at is None or expiry_at <= now):
        return ScreeningStatus.EXPIRED
    return status


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()


def evidence_object(
    *,
    tenant_id: str,
    case_id: str,
    inp: ScreeningInput,
    decision: Decision,
    version: int,
    reviewed: bool = False,
    carry: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """The decision as signed evidence. It holds ids, codes, scores and list versions, never a name or a date of birth."""
    body: dict[str, Any] = {
        "tenant_id": tenant_id,
        "case_id": case_id,
        "version": version,
        "country": inp.country,
        "decision_status": decision.status.value,
        "reasons": list(decision.reasons),
        "authority_verified": decision.authority_verified,
        "authority_kind": inp.authority.kind.value,
        "authority_reference": inp.authority.reference,
        "lists": [{"list_id": a, "version": b, "content_hash": c} for a, b, c in decision.lists],
        "policy_version": inp.policy.version,
        "match_policy_version": inp.policy.match.version,
        "country_pack_version": inp.pack.version if inp.pack else None,
        "parties": [{"ref": s.party.ref, "role": s.role.value} for s in inp.subjects],
        "matches": [
            {
                "party_ref": m.party_ref,
                "list_id": m.list_id.value,
                "list_version": m.list_version,
                "source_ref": m.source_ref,
                "score": m.score,
                "strength": m.strength.value,
                "reasons": list(m.reasons),
            }
            for m in decision.matches
        ],
        "ownership": {
            "flags": list(decision.ownership.flags) if decision.ownership else [],
            "unresolved": list(decision.ownership.unresolved) if decision.ownership else [],
            "identify": list(decision.ownership.identify) if decision.ownership else [],
        },
        "screened_at": inp.now.isoformat(),
        "expiry_at": decision.expiry_at.isoformat() if decision.expiry_at else None,
        "reviewed": reviewed,
    }
    if carry:  # a reviewer's ruling keeps the alerts and ownership findings it answers
        body.update(carry)
    body["evidence_hash"] = hashlib.sha256(_canonical(body)).hexdigest()
    return body


def safe_view(status: ScreeningStatus, reasons: Sequence[str]) -> dict[str, Any]:
    """What a customer administrator sees: the outcome and the next action, never a watchlist or a third party's data."""
    next_action = {
        ScreeningStatus.VERIFIED: "None. The checks are complete.",
        ScreeningStatus.GATHERING: "More information is needed. Provide the details requested.",
        ScreeningStatus.REVIEW_REQUIRED: "Additional review is required. We will contact you if we need anything.",
        ScreeningStatus.UNAVAILABLE: "Checks are temporarily unavailable. Try again later.",
        ScreeningStatus.REJECTED: "This could not be verified. You may appeal.",
        ScreeningStatus.EXPIRED: "The checks are out of date and must be refreshed.",
        ScreeningStatus.SUSPENDED: "Verified privileges are suspended while a review is open.",
    }.get(status, "Checks have not started.")
    return {"status": status.value, "next_action": next_action}


REVIEW_OUTCOMES = frozenset(
    {ScreeningStatus.VERIFIED, ScreeningStatus.REJECTED, ScreeningStatus.REVIEW_REQUIRED}
)


@dataclass(frozen=True, slots=True)
class Ruling:
    reviewer_id: str
    outcome: ScreeningStatus
    rationale: str


def check_ruling(
    current: ScreeningStatus,
    ruling: Ruling,
    *,
    requester_id: str | None,
    subject_refs: Sequence[str],
) -> None:
    if current is not ScreeningStatus.REVIEW_REQUIRED:
        raise ScreeningError("CASE_NOT_IN_REVIEW", current.value)
    if ruling.outcome not in REVIEW_OUTCOMES:
        raise ScreeningError("RULING_OUTCOME_INVALID", ruling.outcome.value)
    if len(ruling.rationale.strip()) < 20:
        raise ScreeningError("RATIONALE_REQUIRED", "a reviewer's decision needs a recorded reason")
    if ruling.reviewer_id == requester_id or ruling.reviewer_id in subject_refs:
        raise ScreeningError(
            "REVIEWER_CONFLICT", "the requester or a screened party cannot decide the case"
        )
