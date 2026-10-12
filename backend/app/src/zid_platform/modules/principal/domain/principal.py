"""Principal and Identifier (ZID-DOM-001 §1, §4, §17, §22; DM-AT-01/02/07/12/15).

- principal_id is immutable, opaque and never derived from PII (DP-01, P-1). Email, employer,
  verification and login state are separate facts that change without changing the principal.
- Identifiers are namespaced attributes with their own lifecycle; the same value in two tenants
  never means the same HUMAN (DM-AT-02).
- Lifecycle is an explicit state machine (DP-10); security_revision only ever increases (INV-11).
- Corrections supersede, never rewrite: a bitemporal history answers both "what is true now"
  and "what did we believe at time T" (TR-01/02/06, DM-AT-07).
Pure domain, no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum


class PrincipalType(StrEnum):
    HUMAN = "HUMAN"
    ORGANIZATION = "ORGANIZATION"
    ORG_UNIT = "ORG_UNIT"
    DEVICE = "DEVICE"
    APPLICATION = "APPLICATION"
    SERVICE = "SERVICE"
    WORKLOAD = "WORKLOAD"
    AI_AGENT = "AI_AGENT"
    EXTERNAL_PRINCIPAL = "EXTERNAL_PRINCIPAL"


class PrincipalStatus(StrEnum):
    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    DISABLED = "DISABLED"
    SUPERSEDED = "SUPERSEDED"
    ERASED = "ERASED"


_TRANSITIONS: dict[PrincipalStatus, frozenset[PrincipalStatus]] = {
    PrincipalStatus.PENDING: frozenset(
        {PrincipalStatus.ACTIVE, PrincipalStatus.DISABLED, PrincipalStatus.ERASED}
    ),
    PrincipalStatus.ACTIVE: frozenset(
        {
            PrincipalStatus.SUSPENDED,
            PrincipalStatus.DISABLED,
            PrincipalStatus.SUPERSEDED,
            PrincipalStatus.ERASED,
        }
    ),
    PrincipalStatus.SUSPENDED: frozenset(
        {PrincipalStatus.ACTIVE, PrincipalStatus.DISABLED, PrincipalStatus.ERASED}
    ),
    PrincipalStatus.DISABLED: frozenset({PrincipalStatus.ACTIVE, PrincipalStatus.ERASED}),
    PrincipalStatus.SUPERSEDED: frozenset({PrincipalStatus.ERASED}),
    PrincipalStatus.ERASED: frozenset(),
}

# Status changes that are security-significant bump security_revision (TR-03).
_SECURITY_SIGNIFICANT = frozenset(
    {
        PrincipalStatus.SUSPENDED,
        PrincipalStatus.DISABLED,
        PrincipalStatus.SUPERSEDED,
        PrincipalStatus.ERASED,
        PrincipalStatus.ACTIVE,
    }
)


class DomainError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


@dataclass(frozen=True, slots=True)
class Principal:
    principal_id: str
    tenant_id: str | None  # None only for platform-scoped trust entities
    principal_type: PrincipalType
    status: PrincipalStatus
    home_cell: str
    security_revision: int = 0
    superseded_by: str | None = None
    # Required by type (§4.2): AI agents need a sponsor; organizations need a profile.
    sponsor_principal_id: str | None = None
    has_organization_profile: bool = False

    def __post_init__(self) -> None:
        if self.security_revision < 0:
            raise DomainError("REVISION_INVALID")
        if self.principal_type is PrincipalType.AI_AGENT and not self.sponsor_principal_id:
            raise DomainError("AGENT_SPONSOR_REQUIRED")
        if self.principal_type is PrincipalType.ORGANIZATION and not self.has_organization_profile:
            raise DomainError("ORGANIZATION_PROFILE_REQUIRED")
        if self.tenant_id is None and self.principal_type not in (
            PrincipalType.ORGANIZATION,
            PrincipalType.SERVICE,
        ):
            raise DomainError("TENANT_REQUIRED")  # only platform trust entities are platform-scoped


def change_status(
    p: Principal, target: PrincipalStatus, *, superseded_by: str | None = None
) -> Principal:
    """Legal lifecycle move; security-significant moves bump security_revision (never down)."""
    if target not in _TRANSITIONS[p.status]:
        raise DomainError("PRINCIPAL_INVALID_TRANSITION", f"{p.status} -> {target}")
    if target is PrincipalStatus.SUPERSEDED and not superseded_by:
        raise DomainError(
            "SUPERSEDED_BY_REQUIRED"
        )  # controlled merge never rewrites old references
    rev = p.security_revision + (1 if target in _SECURITY_SIGNIFICANT else 0)
    return replace(
        p, status=target, security_revision=rev, superseded_by=superseded_by or p.superseded_by
    )


def bump_security_revision(p: Principal) -> Principal:
    """Authenticator change, recovery, compromise signal...: invalidates cached decisions (TR-03)."""
    return replace(p, security_revision=p.security_revision + 1)


# ---------------------------------------------------------------- identifiers


class UniquenessScope(StrEnum):
    GLOBAL = "GLOBAL"
    TENANT = "TENANT"
    ISSUER = "ISSUER"
    ORGANIZATION = "ORGANIZATION"
    NONE = "NONE"


class VerificationState(StrEnum):
    UNVERIFIED = "UNVERIFIED"
    VERIFIED = "VERIFIED"
    STALE = "STALE"
    REVOKED = "REVOKED"


@dataclass(frozen=True, slots=True)
class Identifier:
    identifier_id: str
    principal_id: str
    tenant_id: str
    namespace: str  # email, tel, employee_id, idp:{issuer}, company_number:{jurisdiction}
    value_normalized: str
    uniqueness_scope: UniquenessScope
    verification_state: VerificationState = VerificationState.UNVERIFIED
    scope_key: str = ""  # issuer or organization ID for ISSUER/ORGANIZATION scopes


def normalize(namespace: str, raw: str) -> str:
    """Canonical comparison form. Email: trim + lowercase the domain only (local parts can be case-sensitive)."""
    value = raw.strip()
    if namespace == "email":
        local, _, domain = value.rpartition("@")
        if not local or not domain:
            raise DomainError("IDENTIFIER_INVALID", "email")
        return f"{local}@{domain.lower()}"
    if namespace == "tel":
        digits = "".join(c for c in value if c.isdigit())
        if not value.startswith("+") or not 8 <= len(digits) <= 15:
            raise DomainError("IDENTIFIER_INVALID", "tel must be E.164")
        return f"+{digits}"
    if not value:
        raise DomainError("IDENTIFIER_INVALID", namespace)
    return value


def uniqueness_key(i: Identifier) -> tuple[str, ...] | None:
    """The key under which two identifiers collide; None = no uniqueness (NONE scope).

    TENANT scope keys include the tenant, so the same email in two tenants never collides and never
    implies the same person (DM-AT-02). Collision means "conflict to resolve", never "auto-merge".
    """
    match i.uniqueness_scope:
        case UniquenessScope.NONE:
            return None
        case UniquenessScope.GLOBAL:
            return (i.namespace, i.value_normalized)
        case UniquenessScope.TENANT:
            return (i.tenant_id, i.namespace, i.value_normalized)
        case UniquenessScope.ISSUER | UniquenessScope.ORGANIZATION:
            if not i.scope_key:
                raise DomainError("IDENTIFIER_SCOPE_KEY_REQUIRED")
            return (i.uniqueness_scope.value, i.scope_key, i.namespace, i.value_normalized)


def may_link_same_human(a: Identifier, b: Identifier) -> bool:
    """Two identifier records may be treated as one human only inside one tenant, never across tenants."""
    return (
        a.tenant_id == b.tenant_id
        and uniqueness_key(a) is not None
        and uniqueness_key(a) == uniqueness_key(b)
    )


# ---------------------------------------------------------------- bitemporal history (TR-01/02/06)


@dataclass(frozen=True, slots=True)
class Version[T]:
    value: T
    valid_from: datetime  # business time
    valid_to: datetime | None
    recorded_at: datetime  # system time: when Zoiko learned it
    superseded_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class History[T]:
    versions: tuple[Version[T], ...] = ()

    def record(
        self, value: T, *, valid_from: datetime, valid_to: datetime | None, recorded_at: datetime
    ) -> History[T]:
        """Add a fact or a late correction; nothing is edited or deleted.

        Lookups prefer the most recently recorded version covering the asked-for time, so a
        correction that covers only part of an older version leaves the rest of it in force.
        An older version is marked superseded only when the new one covers its whole interval.
        """
        if self.versions and recorded_at < max(v.recorded_at for v in self.versions):
            raise DomainError("CLOCK_REGRESSION", "system time never goes backwards")

        def covers(v: Version[T]) -> bool:
            starts_before = valid_from <= v.valid_from
            ends_after = valid_to is None or (v.valid_to is not None and v.valid_to <= valid_to)
            return starts_before and ends_after

        out = [
            replace(v, superseded_at=recorded_at) if v.superseded_at is None and covers(v) else v
            for v in self.versions
        ]
        out.append(Version(value, valid_from, valid_to, recorded_at))
        return History(tuple(out))

    def as_of(self, valid_at: datetime, known_at: datetime) -> T | None:
        """What was true at `valid_at`, according to what Zoiko knew at `known_at`."""
        hits = [
            v
            for v in self.versions
            if v.recorded_at <= known_at
            and v.valid_from <= valid_at
            and (v.valid_to is None or valid_at < v.valid_to)
        ]
        return max(hits, key=lambda v: v.recorded_at).value if hits else None
