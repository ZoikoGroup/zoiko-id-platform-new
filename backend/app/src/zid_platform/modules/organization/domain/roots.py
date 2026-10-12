"""Organizational root of trust (ZID-ROOT-001 §0-1, §3, §8, §13-18; FR-ORG-001..008; P0-10).

The three-part root test: the organization is real (ENTITY_EXISTENCE), the holder has the role
(OFFICIAL_ROLE or DELEGATION_BASIS), and that role carries authority for this act (SIGNING_POWER or
DELEGATION_BASIS with a scope that covers the request). Each fact must come from a source
*competent* to establish it, at the assurance and freshness the action's risk requires:

- source quality is not a winner-takes-all ranking: a register proves an appointment, not a
  payment limit (ROOT-16);
- ownership/control (PSC, shareholder, parent company) is never signing authority (ROOT-07/08);
- roots belong to one organization: a parent can't bind a subsidiary, a director of two group
  companies gets entity-specific authority only (ROOT-08/22);
- CONFLICTED and, for R2/R3, STALE roots never yield an allow (ROOT-17/18);
- a restored company does not reactivate old roots; a new officer is only a candidate (ROOT-05/21).
Pure domain, no I/O.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from enum import IntEnum, StrEnum


class Fact(StrEnum):
    """root_capability_class (§13): which kind of fact a root establishes."""

    ENTITY_EXISTENCE = "ENTITY_EXISTENCE"
    OFFICIAL_ROLE = "OFFICIAL_ROLE"
    SIGNING_POWER = "SIGNING_POWER"
    CONTROL = "CONTROL"
    DELEGATION_BASIS = "DELEGATION_BASIS"


class RootType(StrEnum):
    REGISTRY = "REGISTRY"
    VLEI_OOR = "VLEI_OOR"  # official organizational role credential
    VLEI_ECR = "VLEI_ECR"  # engagement context role credential
    BOARD_RESOLUTION = "BOARD_RESOLUTION"
    SIGNING_MATRIX = "SIGNING_MATRIX"
    POWER_OF_ATTORNEY = "POWER_OF_ATTORNEY"
    INSTITUTIONAL_KYB = "INSTITUTIONAL_KYB"
    OTHER = "OTHER"


# Source competence (§3 precedence rule, §8): the facts each root type can establish at all.
COMPETENCE: dict[RootType, frozenset[Fact]] = {
    RootType.REGISTRY: frozenset({Fact.ENTITY_EXISTENCE, Fact.OFFICIAL_ROLE, Fact.CONTROL}),
    RootType.VLEI_OOR: frozenset({Fact.ENTITY_EXISTENCE, Fact.OFFICIAL_ROLE}),
    RootType.VLEI_ECR: frozenset({Fact.ENTITY_EXISTENCE, Fact.OFFICIAL_ROLE}),
    RootType.BOARD_RESOLUTION: frozenset({Fact.SIGNING_POWER, Fact.DELEGATION_BASIS}),
    RootType.SIGNING_MATRIX: frozenset({Fact.SIGNING_POWER}),
    RootType.POWER_OF_ATTORNEY: frozenset({Fact.DELEGATION_BASIS, Fact.SIGNING_POWER}),
    RootType.INSTITUTIONAL_KYB: frozenset({Fact.ENTITY_EXISTENCE, Fact.CONTROL}),
    RootType.OTHER: frozenset(),
}

HOLDER_FACTS = frozenset({Fact.OFFICIAL_ROLE, Fact.DELEGATION_BASIS})
SCOPE_FACTS = frozenset({Fact.SIGNING_POWER, Fact.DELEGATION_BASIS})


class Assurance(IntEnum):
    AR0 = 0
    AR1 = 1
    AR2 = 2
    AR3 = 3


# Highest risk class each assurance may root (ROOT-001 §1.1; AUTHZ §6). AR-2 for R3 only where
# policy/legal review permits, which the caller signals with `r3_on_ar2_permitted`.
MAX_RISK = {Assurance.AR0: -1, Assurance.AR1: 1, Assurance.AR2: 2, Assurance.AR3: 3}


class RootStatus(StrEnum):
    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    STALE = "STALE"
    CONFLICTED = "CONFLICTED"
    SUSPENDED = "SUSPENDED"
    REVOKED = "REVOKED"
    EXPIRED = "EXPIRED"
    SUPERSEDED = "SUPERSEDED"


TERMINAL = frozenset({RootStatus.REVOKED, RootStatus.EXPIRED, RootStatus.SUPERSEDED})

_TRANSITIONS: dict[RootStatus, frozenset[RootStatus]] = {
    RootStatus.PENDING: frozenset({RootStatus.ACTIVE, RootStatus.REVOKED, RootStatus.EXPIRED}),
    RootStatus.ACTIVE: frozenset(
        {
            RootStatus.STALE,
            RootStatus.CONFLICTED,
            RootStatus.SUSPENDED,
            RootStatus.REVOKED,
            RootStatus.EXPIRED,
            RootStatus.SUPERSEDED,
        }
    ),
    RootStatus.STALE: frozenset(
        {RootStatus.ACTIVE, RootStatus.CONFLICTED, RootStatus.REVOKED, RootStatus.EXPIRED}
    ),
    # §17.1: resolution validates the prior fact (ACTIVE, new version), supersedes it, or revokes it.
    RootStatus.CONFLICTED: frozenset(
        {RootStatus.ACTIVE, RootStatus.SUPERSEDED, RootStatus.REVOKED}
    ),
    RootStatus.SUSPENDED: frozenset({RootStatus.ACTIVE, RootStatus.REVOKED, RootStatus.EXPIRED}),
}


class RootError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


def transition(current: RootStatus, target: RootStatus) -> RootStatus:
    if target not in _TRANSITIONS.get(current, frozenset()):
        raise RootError("ROOT_INVALID_TRANSITION", f"{current} -> {target}")
    return target


@dataclass(frozen=True, slots=True)
class RootAssertion:
    """AuthorityRootAssertion (§13), reduced to what resolution needs."""

    root_id: str
    organization_id: str
    holder_principal_id: str
    root_type: RootType
    facts: frozenset[Fact]  # facts this assertion claims (filtered by COMPETENCE before use)
    assurance: Assurance
    status: RootStatus
    jurisdiction: str
    valid_from: datetime
    valid_to: datetime | None
    fresh_until: datetime
    source_revision: str = ""
    # Authority scope for SCOPE_FACTS: None = no scope evidence (a role alone authorizes nothing).
    scope_actions: frozenset[str] | None = None
    value_limit: Decimal | None = (
        None  # None with scope_actions set = no value ceiling in the instrument
    )
    currency: str | None = None

    def competent_facts(self) -> frozenset[Fact]:
        return self.facts & COMPETENCE[self.root_type]


@dataclass(frozen=True, slots=True)
class RootRequest:
    organization_id: str
    holder_principal_id: str
    action: str
    at: datetime
    risk_level: int  # RiskClass value 0-3
    jurisdiction: str
    value: Decimal | None = None
    currency: str | None = None
    r3_on_ar2_permitted: bool = False


@dataclass(frozen=True, slots=True)
class RootResolution:
    root_ids: tuple[str, ...]  # one root, or an explicit composite covering the three facts
    codes: tuple[str, ...] = ()
    valid_until: datetime | None = None

    @property
    def valid(self) -> bool:
        return not self.codes and bool(self.root_ids)


def _usable(root: RootAssertion, req: RootRequest) -> str | None:
    """Why this root can't serve the request at all (None = usable)."""
    if root.organization_id != req.organization_id:
        return "ROOT_WRONG_ORGANIZATION"
    if root.status is RootStatus.CONFLICTED:
        return "ROOT_CONFLICTED" if req.risk_level >= 2 else None
    if root.status is RootStatus.STALE or req.at >= root.fresh_until:
        return "AUTHORITY_STALE" if req.risk_level >= 2 else None
    if root.status is not RootStatus.ACTIVE:
        return "AUTHORITY_REVOKED"
    if req.at < root.valid_from or (root.valid_to is not None and req.at >= root.valid_to):
        return "AUTHORITY_REVOKED"
    if root.jurisdiction != req.jurisdiction:
        return "JURISDICTION_BLOCKED"
    ceiling = MAX_RISK[root.assurance]
    if root.assurance is Assurance.AR2 and req.r3_on_ar2_permitted:
        ceiling = 3
    if req.risk_level > ceiling:
        return "ASSURANCE_INSUFFICIENT"
    return None


def _scope_covers(root: RootAssertion, req: RootRequest) -> bool:
    if root.scope_actions is None or req.action not in root.scope_actions:
        return False
    if req.value is None or root.value_limit is None:
        return True
    return req.currency == root.currency and req.value <= root.value_limit


def resolve_root(req: RootRequest, roots: Iterable[RootAssertion]) -> RootResolution:
    """One complete sufficient root path, or an explicit composite (§15, §15.1). Default deny.

    Every fact category is covered by a root that is itself usable at the action's risk, so a strong
    registry record can't lift a weak authority assertion. The pick is deterministic (sorted ids).
    """
    candidates = sorted(roots, key=lambda r: r.root_id)
    problems: list[str] = []
    usable: list[RootAssertion] = []
    for r in candidates:
        if r.organization_id != req.organization_id:
            continue  # other entities' roots are simply not candidates (ROOT-08/22)
        why = _usable(r, req)
        if why:
            problems.append(why)
        else:
            usable.append(r)
    if any(p in ("ROOT_CONFLICTED",) for p in problems) and req.risk_level >= 2:
        # an unresolved conflict on any candidate for this organization blocks R2/R3 (ROOT-18)
        return RootResolution((), ("ROOT_CONFLICTED",))
    entity = [r for r in usable if Fact.ENTITY_EXISTENCE in r.competent_facts()]
    holder = [
        r
        for r in usable
        if r.holder_principal_id == req.holder_principal_id and r.competent_facts() & HOLDER_FACTS
    ]
    scope = [
        r
        for r in usable
        if r.holder_principal_id == req.holder_principal_id
        and r.competent_facts() & SCOPE_FACTS
        and _scope_covers(r, req)
    ]
    missing = []
    if not entity:
        missing.append("ROOT_ENTITY_UNVERIFIED")
    if not holder:
        missing.append("ROOT_ROLE_UNVERIFIED")
    if not scope:
        missing.append("AUTHORITY_SCOPE_EXCEEDED")
    if missing:
        return RootResolution((), tuple(dict.fromkeys([*missing, *problems])))
    chosen = tuple(dict.fromkeys([entity[0].root_id, holder[0].root_id, scope[0].root_id]))
    picked = [r for r in usable if r.root_id in chosen]
    deadlines = [r.fresh_until for r in picked] + [
        r.valid_to for r in picked if r.valid_to is not None
    ]
    return RootResolution(chosen, (), min(deadlines))


# ---------------------------------------------------------------- organization lifecycle (§18)


class OrgEvent(StrEnum):
    NAME_CHANGE = "NAME_CHANGE"
    OFFICER_APPOINTMENT = "OFFICER_APPOINTMENT"
    OFFICER_CESSATION = "OFFICER_CESSATION"
    DISSOLUTION = "DISSOLUTION"
    RESTORATION = "RESTORATION"
    MERGER = "MERGER"
    OWNERSHIP_CHANGE = "OWNERSHIP_CHANGE"
    REGISTERED_OFFICE_CHANGE = "REGISTERED_OFFICE_CHANGE"


@dataclass(frozen=True, slots=True)
class SourceEvent:
    event_id: str  # source event identity: replays are idempotent (ROOT-24)
    organization_id: str
    kind: OrgEvent
    holder_principal_id: str | None = None


@dataclass(frozen=True, slots=True)
class EventOutcome:
    roots: dict[str, RootAssertion]
    organization_can_originate_authority: bool
    candidate_holders: tuple[str, ...] = ()  # appointments create candidates, never grants
    applied: bool = True


def apply_event(
    event: SourceEvent,
    roots: Mapping[str, RootAssertion],
    *,
    processed_event_ids: frozenset[str],
    organization_active: bool,
) -> EventOutcome:
    """What a source lifecycle event does to an organization's roots (§18)."""
    if event.event_id in processed_event_ids:
        return EventOutcome(dict(roots), organization_active, applied=False)
    out = dict(roots)
    mine = [
        r
        for r in roots.values()
        if r.organization_id == event.organization_id and r.status not in TERMINAL
    ]
    active = organization_active
    if event.kind is OrgEvent.OFFICER_CESSATION:
        for r in mine:
            if r.holder_principal_id == event.holder_principal_id:
                out[r.root_id] = replace(r, status=RootStatus.REVOKED)
    elif event.kind is OrgEvent.DISSOLUTION:
        for r in mine:
            out[r.root_id] = replace(r, status=RootStatus.SUSPENDED)
        active = False
    elif event.kind is OrgEvent.RESTORATION:
        active = (
            True  # the entity exists again, but suspended/revoked roots stay as they are (ROOT-21)
        )
    elif event.kind is OrgEvent.MERGER:
        for r in mine:  # successor/predecessor: everything must be revalidated (ROOT-20)
            if r.status is RootStatus.ACTIVE:
                out[r.root_id] = replace(r, status=RootStatus.STALE)
    elif event.kind is OrgEvent.OFFICER_APPOINTMENT:
        return EventOutcome(
            out, active, (event.holder_principal_id,) if event.holder_principal_id else ()
        )
    # NAME_CHANGE, OWNERSHIP_CHANGE, REGISTERED_OFFICE_CHANGE: entity metadata only, no authority change
    return EventOutcome(out, active)
