"""Authority grants, roots and chain resolution (ZID-MANDATE-001 §2-7; ZID-AUTHZ-001 §5-6, §9; DEC-C1, DEC-C2).

- A grant is valid only as part of one complete chain that ends at a qualifying root.
- Issuance checks the child against the parent's *effective* authority (MANDATE §2.1, §4).
- Resolution walks child -> parent -> root, intersecting constraints and rejecting cycles,
  missing links, stale roots and cross-tenant hops (MANDATE §7).
- Authorization needs ONE chain that covers the whole request. Partial grants are never unioned
  (no privilege stitching, AUTHZ §5.1 / MANDATE §4.1); composite authority is a separate,
  explicit construct.
Pure domain, no I/O: callers load grants and roots and pass them in.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import IntEnum, StrEnum

from zid_platform.modules.authority.domain.algebra import ANY, Authority, Request, Window, uncovered

PLATFORM_MAX_CHAIN_DEPTH = (
    16  # bounded platform-wide even if tenant policy allows fewer (MANDATE §7.2)
)


class GrantType(StrEnum):
    PERSONAL = "PERSONAL"
    ROLE_ISSUED = "ROLE_ISSUED"
    ORGANIZATION_DIRECT = "ORGANIZATION_DIRECT"
    SERVICE_DELEGATION = "SERVICE_DELEGATION"
    AGENT_DELEGATION = "AGENT_DELEGATION"
    EXTERNAL_DELEGATION = "EXTERNAL_DELEGATION"


class ContinuityMode(StrEnum):
    """DEC-C2 / CR-001: one enum used everywhere."""

    PERSON_BOUND = "PERSON_BOUND"
    ROLE_CONTINUOUS = "ROLE_CONTINUOUS"
    ORGANIZATION_CONTINUOUS = "ORGANIZATION_CONTINUOUS"
    CONTRACT_BOUND = "CONTRACT_BOUND"  # proposed by CR-001; pending spec acceptance


def continuity_contained(parent: ContinuityMode, child: ContinuityMode) -> bool:
    """A child cannot adopt broader continuity than its parent (MANDATE §4).

    PERSON_BOUND is the narrowest and fits under anything; ORGANIZATION_CONTINUOUS is the
    broadest and admits anything. ROLE and CONTRACT continuity are different bases, so neither
    can turn into the other.
    """
    return (
        child is parent
        or child is ContinuityMode.PERSON_BOUND
        or parent is ContinuityMode.ORGANIZATION_CONTINUOUS
    )


class GrantStatus(StrEnum):
    DRAFT = "DRAFT"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    ACTIVE = "ACTIVE"
    REVALIDATION_PENDING = "REVALIDATION_PENDING"  # CR-002 (DEC-C2 §4-5)
    SUSPENDED = "SUSPENDED"
    REVOKED = "REVOKED"
    EXPIRED = "EXPIRED"
    SUPERSEDED = "SUPERSEDED"


TERMINAL_GRANT_STATES = frozenset(
    {GrantStatus.REVOKED, GrantStatus.EXPIRED, GrantStatus.SUPERSEDED}
)

_GRANT_TRANSITIONS: dict[GrantStatus, frozenset[GrantStatus]] = {
    GrantStatus.DRAFT: frozenset(
        {GrantStatus.PENDING_APPROVAL, GrantStatus.ACTIVE, GrantStatus.REVOKED}
    ),
    GrantStatus.PENDING_APPROVAL: frozenset({GrantStatus.ACTIVE, GrantStatus.REVOKED}),
    GrantStatus.ACTIVE: frozenset(
        {
            GrantStatus.SUSPENDED,
            GrantStatus.REVALIDATION_PENDING,
            GrantStatus.REVOKED,
            GrantStatus.EXPIRED,
            GrantStatus.SUPERSEDED,
        }
    ),
    GrantStatus.REVALIDATION_PENDING: frozenset(
        {GrantStatus.ACTIVE, GrantStatus.SUSPENDED, GrantStatus.REVOKED, GrantStatus.EXPIRED}
    ),
    GrantStatus.SUSPENDED: frozenset(
        {GrantStatus.ACTIVE, GrantStatus.REVOKED, GrantStatus.EXPIRED}
    ),
    GrantStatus.REVOKED: frozenset(),
    GrantStatus.EXPIRED: frozenset(),
    GrantStatus.SUPERSEDED: frozenset(),
}


class AuthorityError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


def transition(current: GrantStatus, target: GrantStatus) -> GrantStatus:
    """REVOKED, EXPIRED and SUPERSEDED never return to ACTIVE; a new grant is created instead."""
    if target not in _GRANT_TRANSITIONS[current]:
        raise AuthorityError("GRANT_INVALID_TRANSITION", f"{current} -> {target}")
    return target


class RootAssurance(IntEnum):
    """AUTHZ §6 authority root classes."""

    AR0 = 0  # self-asserted: display/context only, never roots material authority
    AR1 = 1  # tenant-attested, low assurance
    AR2 = 2  # verified institutional evidence, board resolution, signing matrix
    AR3 = 3  # authoritative registry, vLEI role or equivalent


# Highest risk class (RiskClass value 0-3) each root class may root (AUTHZ §6; DEC-C2 §2).
MAX_RISK_ROOTED = {
    RootAssurance.AR0: -1,
    RootAssurance.AR1: 1,
    RootAssurance.AR2: 2,
    RootAssurance.AR3: 3,
}


class RootStatus(StrEnum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    REVOKED = "REVOKED"
    SUPERSEDED = "SUPERSEDED"
    EXPIRED = "EXPIRED"


@dataclass(frozen=True, slots=True)
class AuthorityRoot:
    """AuthorityRootAssertion: evidence that `holder` may originate authority for an organization."""

    root_id: str
    tenant_id: str
    organization_id: str
    holder_principal_id: str
    assurance: RootAssurance
    status: RootStatus
    window: Window
    fresh_until: datetime  # freshness deadline for the evidence (registry refresh, re-attestation)
    authority: Authority = field(default_factory=Authority)  # what the root itself may authorize
    revision: int = 0


@dataclass(frozen=True, slots=True)
class Grant:
    grant_id: str
    tenant_id: str
    grant_type: GrantType
    grantor_principal_id: str
    grantee_principal_id: str
    represented_principal_id: str
    authority: Authority  # requested constraints of this grant (window included)
    continuity_mode: ContinuityMode
    status: GrantStatus
    issued_at: datetime
    root_id: str | None = None  # required on the top grant of a chain
    parent_grant_id: str | None = None
    delegation_allowed: bool = False
    max_delegation_depth: int = 0
    revision: int = 0


# ---------------------------------------------------------------- resolution


@dataclass(frozen=True, slots=True)
class Resolution:
    """AuthorityResolution (AUTHZ §6.1). `valid` is False whenever any code is present."""

    chain_ids: tuple[str, ...]  # leaf first, top grant last
    root_id: str | None
    effective: Authority | None
    codes: tuple[str, ...] = ()
    person_bindings: frozenset[str] = (
        frozenset()
    )  # principals whose personal authority the chain relies on

    @property
    def valid(self) -> bool:
        return not self.codes and self.effective is not None


def _fail(chain: list[str], code: str, root_id: str | None = None) -> Resolution:
    return Resolution(tuple(chain), root_id, None, (code,))


def resolve_chain(
    grant_id: str,
    *,
    tenant_id: str,
    grants: Mapping[str, Grant],
    roots: Mapping[str, AuthorityRoot],
    now: datetime,
    risk_level: int,
) -> Resolution:
    """Build and validate the chain for `grant_id` (MANDATE §7.1, AUTHZ §6.1).

    `risk_level` is the RiskClass value (0-3) of the action, used for root eligibility.
    """
    # Cycle check first, over parent links only, so a cyclic chain is always reported as a cycle.
    seen: list[str] = []
    walk: str | None = grant_id
    while walk is not None and walk in grants:
        if walk in seen:
            return _fail(seen, "AUTHORITY_CYCLE")
        seen.append(walk)
        walk = grants[walk].parent_grant_id
    chain: list[str] = []
    effective = Authority()
    bindings: set[str] = set()
    child: Grant | None = None
    current: str | None = grant_id
    while current is not None:
        if current in chain:
            return _fail(chain, "AUTHORITY_CYCLE")
        if len(chain) >= PLATFORM_MAX_CHAIN_DEPTH:
            return _fail(chain, "DELEGATION_DEPTH_EXCEEDED")
        grant = grants.get(current)
        if grant is None:
            return _fail(chain, "AUTHORITY_MISSING")  # never skip a missing parent
        chain.append(current)
        if grant.tenant_id != tenant_id and grant.grant_type is not GrantType.EXTERNAL_DELEGATION:
            return _fail(chain, "AUTHORITY_CROSS_TENANT")
        if grant.status is GrantStatus.REVALIDATION_PENDING:
            return _fail(chain, "AUTHORITY_STALE")
        if grant.status is not GrantStatus.ACTIVE:
            return _fail(chain, "AUTHORITY_REVOKED")
        if not grant.authority.window.includes(now):
            return _fail(chain, "AUTHORITY_REVOKED")
        if child is not None:
            # the parent must have allowed this delegation, with depth strictly decreasing
            if not grant.delegation_allowed:
                return _fail(chain, "GRANT_DELEGATION_NOT_ALLOWED")
            if child.max_delegation_depth >= grant.max_delegation_depth:
                return _fail(chain, "DELEGATION_DEPTH_EXCEEDED")
            if child.grantor_principal_id != grant.grantee_principal_id:
                return _fail(chain, "GRANTOR_AUTHORITY_MISSING")
            if not continuity_contained(grant.continuity_mode, child.continuity_mode):
                return _fail(chain, "GRANT_SCOPE_WIDENING")
        if grant.continuity_mode is ContinuityMode.PERSON_BOUND:
            bindings.add(grant.grantor_principal_id)
        effective = effective.intersect(grant.authority)
        child = grant
        current = grant.parent_grant_id

    top = child
    assert top is not None
    root = roots.get(top.root_id) if top.root_id else None
    if root is None:
        return _fail(chain, "AUTHORITY_MISSING")  # chain must terminate at a qualifying root
    if root.tenant_id != tenant_id:
        return _fail(chain, "AUTHORITY_CROSS_TENANT", root.root_id)
    if root.status is not RootStatus.ACTIVE or not root.window.includes(now):
        return _fail(chain, "AUTHORITY_REVOKED", root.root_id)
    if now >= root.fresh_until:
        return _fail(chain, "AUTHORITY_STALE", root.root_id)
    if risk_level > MAX_RISK_ROOTED[root.assurance]:
        return _fail(chain, "ASSURANCE_INSUFFICIENT", root.root_id)
    if top.grantor_principal_id != root.holder_principal_id:
        return _fail(chain, "GRANTOR_AUTHORITY_MISSING", root.root_id)
    if not root.window.includes(top.issued_at):
        return _fail(chain, "AUTHORITY_REVOKED", root.root_id)
    effective = effective.intersect(root.authority).intersect(Authority(window=root.window))
    return Resolution(tuple(chain), root.root_id, effective, (), frozenset(bindings))


@dataclass(frozen=True, slots=True)
class Decision:
    allowed: bool
    resolution: Resolution | None  # the ONE chain that covered the request
    codes: tuple[str, ...]


def authorize(
    request: Request,
    candidate_grant_ids: Iterable[str],
    *,
    tenant_id: str,
    grants: Mapping[str, Grant],
    roots: Mapping[str, AuthorityRoot],
    risk_level: int,
) -> Decision:
    """ALLOW only if one complete valid chain covers the whole request. Default deny.

    Grants are tried one chain at a time; dimensions covered by different grants are never
    combined. With no candidates the answer is AUTHORITY_MISSING.
    """
    codes: list[str] = []
    for gid in sorted(set(candidate_grant_ids)):
        res = resolve_chain(
            gid,
            tenant_id=tenant_id,
            grants=grants,
            roots=roots,
            now=request.at,
            risk_level=risk_level,
        )
        if not res.valid:
            codes.extend(res.codes)
            continue
        assert res.effective is not None
        if not uncovered(res.effective, request):
            return Decision(True, res, ("AUTHORITY_VALID",))
        codes.append("AUTHORITY_SCOPE_EXCEEDED")
    return Decision(False, None, tuple(dict.fromkeys(codes)) or ("AUTHORITY_MISSING",))


# ---------------------------------------------------------------- issuance


def validate_child_grant(
    child: Grant,
    *,
    grants: Mapping[str, Grant],
    roots: Mapping[str, AuthorityRoot],
    now: datetime,
    risk_level: int,
) -> list[str]:
    """Issuance gate for a delegated grant (MANDATE §2.1, §4). Empty list = may become ACTIVE."""
    if child.parent_grant_id is None:
        return ["GRANTOR_AUTHORITY_MISSING"]
    parent_res = resolve_chain(
        child.parent_grant_id,
        tenant_id=child.tenant_id,
        grants=grants,
        roots=roots,
        now=now,
        risk_level=risk_level,
    )
    if not parent_res.valid:
        return ["GRANTOR_AUTHORITY_MISSING", *parent_res.codes]
    parent = grants[child.parent_grant_id]
    problems: list[str] = []
    if child.grantor_principal_id != parent.grantee_principal_id:
        problems.append("GRANTOR_AUTHORITY_MISSING")  # no self-certified authority
    if not parent.delegation_allowed:
        problems.append("GRANT_DELEGATION_NOT_ALLOWED")
    if child.max_delegation_depth >= parent.max_delegation_depth or child.max_delegation_depth < 0:
        problems.append("GRANT_DEPTH_EXCEEDED")
    if len(parent_res.chain_ids) + 1 > PLATFORM_MAX_CHAIN_DEPTH:
        problems.append("GRANT_DEPTH_EXCEEDED")
    assert parent_res.effective is not None
    if parent_res.effective.widenings(child.authority):
        problems.append("GRANT_SCOPE_WIDENING")
    if not continuity_contained(parent.continuity_mode, child.continuity_mode):
        problems.append("GRANT_SCOPE_WIDENING")
    if (
        child.tenant_id != parent.tenant_id
        and child.grant_type is not GrantType.EXTERNAL_DELEGATION
    ):
        problems.append("AUTHORITY_CROSS_TENANT")
    return list(dict.fromkeys(problems))


# ---------------------------------------------------------------- continuity cascade (DEC-C2 §4)


class ContinuityEffect(StrEnum):
    INEFFECTIVE = "INEFFECTIVE"  # PERSON_BOUND: revoke
    REVALIDATE_ROLE = (
        "REVALIDATE_ROLE"  # ROLE_CONTINUOUS: REVALIDATION_PENDING until role + root revalidate
    )
    REVALIDATE_ORGANIZATION = (
        "REVALIDATE_ORGANIZATION"  # ORGANIZATION_CONTINUOUS: re-check org policy
    )
    RECHECK_CONTRACT = (
        "RECHECK_CONTRACT"  # CONTRACT_BOUND: unaffected by personnel; re-check contract root
    )
    UNAFFECTED = "UNAFFECTED"


_ON_ROOT_CHANGE = {
    ContinuityMode.PERSON_BOUND: ContinuityEffect.INEFFECTIVE,
    ContinuityMode.ROLE_CONTINUOUS: ContinuityEffect.REVALIDATE_ROLE,
    ContinuityMode.ORGANIZATION_CONTINUOUS: ContinuityEffect.REVALIDATE_ORGANIZATION,
    ContinuityMode.CONTRACT_BOUND: ContinuityEffect.RECHECK_CONTRACT,
}


def descendants(grant_id: str, grants: Mapping[str, Grant]) -> set[str]:
    children: dict[str, list[str]] = {}
    for g in grants.values():
        if g.parent_grant_id is not None:
            children.setdefault(g.parent_grant_id, []).append(g.grant_id)
    out: set[str] = set()
    stack = [grant_id]
    while stack:
        for c in children.get(stack.pop(), []):
            if c not in out:
                out.add(c)
                stack.append(c)
    return out


def on_officeholder_change(
    root_id: str, grants: Mapping[str, Grant]
) -> dict[str, ContinuityEffect]:
    """Officer removed or replaced: the root is revoked/superseded (MANDATE §5.1, DEC-C2 §4).

    Grants issued directly under the root follow their own continuity mode. A descendant can never
    keep authority its ancestor lost: if an ancestor becomes INEFFECTIVE, every descendant does
    too, whatever mode it declares (DEC-C2 §3).
    """
    effects: dict[str, ContinuityEffect] = {}
    for g in grants.values():
        if (
            g.root_id == root_id
            and g.parent_grant_id is None
            and g.status not in TERMINAL_GRANT_STATES
        ):
            effects[g.grant_id] = _ON_ROOT_CHANGE[g.continuity_mode]
    for top_id, effect in list(effects.items()):
        for d in descendants(top_id, grants):
            if grants[d].status in TERMINAL_GRANT_STATES:
                continue
            if effect is ContinuityEffect.INEFFECTIVE:
                effects[d] = ContinuityEffect.INEFFECTIVE
            elif grants[d].continuity_mode is ContinuityMode.PERSON_BOUND:
                effects.setdefault(d, ContinuityEffect.UNAFFECTED)
            else:
                effects.setdefault(d, effect)
    return effects


def on_personal_authority_ended(principal_id: str, grants: Mapping[str, Grant]) -> set[str]:
    """Grants that become ineffective when `principal_id` loses qualifying authority (AT-07, MAND-02).

    Every PERSON_BOUND grant the principal issued, and every descendant of it, whatever mode the
    descendant declares: no descendant survives a broken PERSON_BOUND ancestor.
    """
    hit: set[str] = set()
    for g in grants.values():
        if (
            g.continuity_mode is ContinuityMode.PERSON_BOUND
            and g.grantor_principal_id == principal_id
            and g.status not in TERMINAL_GRANT_STATES
        ):
            hit.add(g.grant_id)
            hit |= descendants(g.grant_id, grants)
    return {h for h in hit if grants[h].status not in TERMINAL_GRANT_STATES}


__all__ = [
    "ANY",
    "PLATFORM_MAX_CHAIN_DEPTH",
    "AuthorityError",
    "AuthorityRoot",
    "ContinuityEffect",
    "ContinuityMode",
    "Decision",
    "Grant",
    "GrantStatus",
    "GrantType",
    "Resolution",
    "RootAssurance",
    "RootStatus",
    "authorize",
    "continuity_contained",
    "descendants",
    "on_officeholder_change",
    "on_personal_authority_ended",
    "resolve_chain",
    "transition",
    "validate_child_grant",
]
