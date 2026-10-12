"""Relationship facts (ZID-DOM-001 §5; ZID-AUTHZ-001 §7; INV R-1, DM-AT-03/12/13).

A relationship is a typed, scoped, temporal fact, never action authority by itself. "CFO of the
company" or "owns the device" does not transfer funds. Every material mutation advances the
tenant's relation_revision, which only increases, so decisions can bind to it and a projection
known to be older than the required revision can't produce an R2/R3 allow. Pure domain, no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum


class RelationshipType(StrEnum):
    MEMBER_OF = "MEMBER_OF"
    EMPLOYED_BY = "EMPLOYED_BY"
    OWNS = "OWNS"
    MANAGES = "MANAGES"
    CONTROLS = "CONTROLS"
    OPERATES = "OPERATES"
    SPONSORS = "SPONSORS"
    CREATED_FROM = "CREATED_FROM"
    PARENT_OF = "PARENT_OF"
    ASSIGNED_ROLE = "ASSIGNED_ROLE"


class RelationshipStatus(StrEnum):
    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    EXPIRED = "EXPIRED"
    REVOKED = "REVOKED"
    SUPERSEDED = "SUPERSEDED"


_TRANSITIONS: dict[RelationshipStatus, frozenset[RelationshipStatus]] = {
    RelationshipStatus.PENDING: frozenset({RelationshipStatus.ACTIVE, RelationshipStatus.REVOKED}),
    RelationshipStatus.ACTIVE: frozenset(
        {RelationshipStatus.EXPIRED, RelationshipStatus.REVOKED, RelationshipStatus.SUPERSEDED}
    ),
}


class RelationshipError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


@dataclass(frozen=True, slots=True)
class Relationship:
    relationship_id: str
    tenant_id: str
    subject_principal_id: str
    subject_tenant_id: str
    relationship_type: RelationshipType
    object_ref: str
    object_tenant_id: str
    source: str
    assurance: int
    status: RelationshipStatus
    valid_from: datetime
    valid_to: datetime | None
    relation_revision: int
    cross_tenant_trust_id: str | None = None  # explicit federation/trust construct (INV-01)

    def __post_init__(self) -> None:
        crosses = (
            self.subject_tenant_id != self.tenant_id or self.object_tenant_id != self.tenant_id
        )
        if crosses and not self.cross_tenant_trust_id:
            # rejected at domain validation, before anything is persisted (DM-AT-12)
            raise RelationshipError("CROSS_TENANT_REFERENCE")

    def is_current(self, at: datetime) -> bool:
        return (
            self.status is RelationshipStatus.ACTIVE
            and self.valid_from <= at
            and (self.valid_to is None or at < self.valid_to)
        )


@dataclass(frozen=True, slots=True)
class RevisionClock:
    """Per-tenant monotonic relation_revision (TR-04, INV-11)."""

    tenant_id: str
    current: int = 0

    def advance(self) -> RevisionClock:
        return replace(self, current=self.current + 1)


def mutate(
    rel: Relationship, target: RelationshipStatus, clock: RevisionClock
) -> tuple[Relationship, RevisionClock]:
    """Every material change moves the tenant revision forward and stamps the edge with it."""
    if rel.tenant_id != clock.tenant_id:
        raise RelationshipError("CROSS_TENANT_REFERENCE")
    if target not in _TRANSITIONS.get(rel.status, frozenset()):
        raise RelationshipError("RELATIONSHIP_INVALID_TRANSITION", f"{rel.status} -> {target}")
    clock = clock.advance()
    return replace(rel, status=target, relation_revision=clock.current), clock


def projection_fresh_enough(
    projection_revision: int, required_revision: int, risk_level: int
) -> bool:
    """R2/R3 never allow from a projection known to be older than the required revision (AUTHZ §7.1).

    R0/R1 may read bounded-stale within policy TTL, which the caller enforces separately.
    """
    return risk_level < 2 or projection_revision >= required_revision
