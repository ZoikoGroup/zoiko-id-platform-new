"""The organization gate of tenant onboarding (OVERLAY-001 §2, §4; ROOT-001 §19; TENANCY §14 step 7;
G-W1).

DRAFT -> ORGANIZATION_VERIFYING -> CONFIGURING, driven by the pending bootstrap OWNER:

1. the tenant enters ORGANIZATION_VERIFYING (the submission is recorded even if verification fails);
2. the registry adapter resolves the company; the representative must be a *current* official officer,
   bound by an exact match of their verified legal-name claim (IAL-Z2+), never by self-assertion;
3. an AR-3 REGISTRY root is established with the representative as holder;
4. the tenant is seeded: the platform baseline policy release (authority decides), and one
   PERSON_BOUND grant from the root holder to themself carrying tenant-administration actions only
   (OWNER is never business signing authority; if the officer ceases office, the cascade revokes it);
5. the bootstrap OWNER becomes ACTIVE and the tenant moves to CONFIGURING with the gate report.
Any gate failure leaves the tenant in ORGANIZATION_VERIFYING with the failing report recorded.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection
from zid_context import uuid7
from zid_db import in_tenant

from zid_platform.modules.authority.ports import (
    Authority,
    ContinuityMode,
    Grant,
    GrantStatus,
    GrantType,
    Window,
    issue_grant,
    load_grants_for_grantee,
)
from zid_platform.modules.onboarding.domain.gates import (
    OWNER_ONLY_ACTIONS,
    TENANT_ADMIN_ACTIONS,
    Gate,
    GateStatus,
    OnboardingError,
    owner_actions,
)
from zid_platform.modules.organization.ports import (
    EstablishedRoot,
    RegistryAdapter,
    RegistryError,
    establish_development_root,
    establish_registry_root,
)
from zid_platform.modules.policy_authoring.ports import seed_baseline_release
from zid_platform.modules.tenant.ports import (
    Move,
    Role,
    RoleStatus,
    TenantStatus,
    activate_bootstrap_owner,
    live_roles,
    load_tenant,
    set_fields,
    transition,
)
from zid_platform.modules.verification.ports import claim_matches

ADMIN_GRANT_TERM = timedelta(days=365)
NAME_CLAIMS = ("legal_name", "full_name")


@dataclass(frozen=True, slots=True)
class OrganizationResult:
    status: str  # the tenant status afterwards
    gates: tuple[Gate, ...]
    root: EstablishedRoot | None
    admin_grant_id: str | None


def _gate(status: GateStatus, code: str | None = None, detail: str = "") -> Gate:
    return Gate(
        "organization.root", TenantStatus.ORGANIZATION_VERIFYING.value, status, code, detail
    )


async def submit_organization(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    principal_id: str,
    adapter: RegistryAdapter,
    identifier: str,
    corroborate: Sequence[tuple[RegistryAdapter, str]] = (),
    catalog: Mapping[str, Mapping[str, Any]],
    now: datetime | None = None,
) -> OrganizationResult:
    now = now or datetime.now(UTC)
    tenant = await load_tenant(conn, tenant_id, lock=True)
    if tenant is None:
        raise OnboardingError("TENANT_NOT_FOUND")
    pending = [
        a
        for a in await live_roles(conn, principal_id)
        if a.role is Role.OWNER and a.status is RoleStatus.PENDING_VERIFICATION
    ]
    if not pending:
        raise OnboardingError(
            "TENANT_OWNER_UNAUTHORIZED",
            "Only the pending bootstrap OWNER submits the organization.",
        )
    if tenant.status is TenantStatus.DRAFT:
        tenant = await transition(
            conn,
            tenant_id,
            Move.ADVANCE,
            reason_code="ORGANIZATION_SUBMITTED",
            actor=principal_id,
            now=now,
        )
    if tenant.status is not TenantStatus.ORGANIZATION_VERIFYING:
        raise OnboardingError("TENANT_STATE_INVALID", f"The tenant is {tenant.status}.")

    async def matches(candidates: tuple[str, ...]) -> bool:
        return await claim_matches(
            conn, principal_id, claims=NAME_CLAIMS, candidates=candidates, now=now
        )

    try:
        root = await establish_registry_root(
            conn,
            tenant_id,
            adapter=adapter,
            identifier=identifier,
            holder_principal_id=principal_id,
            name_matches=matches,
            now=now,
            corroborate=corroborate,
        )
    except RegistryError as e:
        if e.code == "REGISTRY_UNAVAILABLE":
            raise OnboardingError(e.code, e.detail) from e  # retry later; nothing is concluded
        gate = _gate(GateStatus.FAIL, "TENANT_ORGANIZATION_UNVERIFIED", f"{e.code}: {e.detail}")
        return OrganizationResult(TenantStatus.ORGANIZATION_VERIFYING.value, (gate,), None, None)

    return await _seed_and_advance(
        conn,
        tenant_id,
        principal_id=principal_id,
        root=root,
        catalog=catalog,
        detail=f"{root.source_id}:{root.namespace}",
        now=now,
    )


def _earlier(a: datetime, b: datetime | None) -> datetime:
    return a if b is None else min(a, b)


async def seed_administrator_grant(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    admin_principal_id: str,
    inviter_principal_id: str,
    catalog: Mapping[str, Mapping[str, Any]],
    now: datetime | None = None,
) -> str | None:
    """An invited Administrator accepts: the inviting owner delegates tenant administration to them.

    The grant is a child of the owner's own grant, so it can carry nothing the owner does not hold, is
    PERSON_BOUND (it ends with the person), cannot be delegated further, and is never business authority. It is what
    lets a second person approve the owner's protected steps. Returns the grant id, or None when the inviter holds no
    delegable administration grant (an owner seeded before delegation existed): the role then confers nothing."""
    now = now or datetime.now(UTC)
    eligible = [
        g
        for g in await load_grants_for_grantee(conn, inviter_principal_id)
        if g.status is GrantStatus.ACTIVE
        and g.delegation_allowed
        and g.max_delegation_depth >= 1
        and g.authority.window.valid_to is not None
        and g.authority.window.valid_to > now
    ]

    def covers(g: Grant) -> int:
        held = g.authority.actions
        return (
            len(TENANT_ADMIN_ACTIONS)
            if not isinstance(held, frozenset)
            else len(held & set(TENANT_ADMIN_ACTIONS))
        )

    # The grant that carries the most current administration actions, newest first: a refreshed owner grant (more
    # actions) must win over the one issued at registration, or a new action never reaches the administrators.
    parent = max(eligible, key=lambda g: (covers(g), g.issued_at), default=None)
    held = parent.authority.actions if parent is not None else None
    if parent is None or not isinstance(held, frozenset):
        return None
    actions = tuple(
        a
        for a in TENANT_ADMIN_ACTIONS
        if a in catalog and a in held and a not in OWNER_ONLY_ACTIONS
    )
    if not actions:
        return None
    grant_id = str(uuid7())
    await issue_grant(
        conn,
        Grant(
            grant_id,
            tenant_id,
            GrantType.ROLE_ISSUED,
            inviter_principal_id,
            admin_principal_id,
            parent.represented_principal_id,
            replace(  # everything else the owner's grant limits stays limited
                parent.authority,
                actions=frozenset(actions),
                window=Window(
                    now - timedelta(seconds=1),
                    _earlier(now + ADMIN_GRANT_TERM, parent.authority.window.valid_to),
                ),
            ),
            ContinuityMode.PERSON_BOUND,
            GrantStatus.ACTIVE,
            now,
            parent_grant_id=parent.grant_id,
        ),
        risk_level=3,
        now=now,
    )
    return grant_id


async def _seed_and_advance(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    principal_id: str,
    root: EstablishedRoot,
    catalog: Mapping[str, Mapping[str, Any]],
    detail: str,
    now: datetime,
    risk_level: int = 3,
) -> OrganizationResult:
    """Steps 4-5: baseline release, the holder's PERSON_BOUND tenant-admin grant, OWNER active,
    ORGANIZATION_VERIFYING -> CONFIGURING with the gate report."""
    await set_fields(conn, tenant_id, organization_id=root.organization_id)
    actions = owner_actions(
        catalog
    )  # CR-137: every customer-side action, not only the administrator list
    await seed_baseline_release(
        conn, tenant_id, example_action="tenant.member.manage", catalog=catalog, now=now
    )
    grant_id = str(uuid7())
    await issue_grant(
        conn,
        Grant(
            grant_id,
            tenant_id,
            GrantType.ROLE_ISSUED,
            principal_id,  # the root holder issues it (MANDATE §2.1: the grantor is authenticated)
            principal_id,
            root.organization_id,
            Authority(
                actions=frozenset(actions),
                window=Window(now - timedelta(seconds=1), now + ADMIN_GRANT_TERM),
            ),
            ContinuityMode.PERSON_BOUND,
            GrantStatus.ACTIVE,
            now,
            root_id=root.root_id,
            # The owner may hand tenant administration to an Administrator one level down (never further), so a
            # company can get its second approver without a platform operator (OVERLAY §4).
            delegation_allowed=True,
            max_delegation_depth=1,
        ),
        risk_level=risk_level,
        now=now,
    )
    await activate_bootstrap_owner(conn, tenant_id, now=now)
    gate = _gate(GateStatus.PASS, detail=detail)
    tenant = await transition(
        conn,
        tenant_id,
        Move.ADVANCE,
        reason_code="ORGANIZATION_VERIFIED",
        actor=principal_id,
        gate_report=[gate.view()],
        now=now,
    )
    return OrganizationResult(tenant.status.value, (gate,), root, grant_id)


async def establish_from_verified_claim(
    conn: AsyncConnection,
    tenant_id: str,
    claim_id: str,
    *,
    adapters: Mapping[str, Any],
    catalog: Mapping[str, Mapping[str, Any]],
    now: datetime | None = None,
) -> OrganizationResult:
    """ZID-UX-WF-013B to 13C: the organization exists only once its claim is VERIFIED, which is only when all four checks (the entity, the applicant's
    identity, the authority and the screening) are complete, each by its own service. Then, and only then, the claimant's pending OWNER becomes active,
    the workspace takes the register's legal name (never the name the applicant typed) and the organization moves on to setup. Idempotent: a workspace that
    already has its organization is answered as it stands. Nothing here reads a registry match or a declaration as the claim being verified."""
    from zid_platform.modules.onboarding.application import claims
    from zid_platform.modules.organization.ports import establish_claim_root, kyb_get_case

    now = now or datetime.now(UTC)
    tenant = await load_tenant(conn, tenant_id, lock=True)
    if tenant is None:
        raise OnboardingError("TENANT_NOT_FOUND")
    if tenant.organization_id:
        return OrganizationResult(
            tenant.status.value, (_gate(GateStatus.PASS, detail="established"),), None, None
        )
    seen = await claims.status(conn, tenant_id, claim_id, now)
    if seen["outcome"] != "VERIFIED":
        raise OnboardingError(
            "CLAIM_NOT_VERIFIED", "The claim is not verified: every check must be complete first."
        )
    case = await kyb_get_case(conn, tenant_id, claim_id, now=now)
    pending = [
        a
        for a in await live_roles(conn, case.requested_by)
        if a.role is Role.OWNER and a.status is RoleStatus.PENDING_VERIFICATION
    ]
    if not pending:
        raise OnboardingError(
            "TENANT_OWNER_UNAUTHORIZED",
            "Only the claimant's pending OWNER role is activated by a verified claim.",
        )
    if claims.dev_shortcut_enabled():
        # LOCAL/TEST ONLY (refused at start-up anywhere else): no register lookup. The synthetic root says what it is, exactly as `development_verification`.
        name = (case.legal_name or "Company").strip()[
            :200
        ] or "Company"  # the name the applicant gave, never a register's
        if tenant.status is TenantStatus.DRAFT:
            await transition(
                conn,
                tenant_id,
                Move.ADVANCE,
                reason_code="ORGANIZATION_SUBMITTED",
                actor=case.requested_by,
                now=now,
            )
        root = await establish_development_root(
            conn, tenant_id, holder_principal_id=case.requested_by, legal_name=name, now=now
        )
        await set_fields(conn, tenant_id, display_name=name)
        return await _seed_and_advance(
            conn,
            tenant_id,
            principal_id=case.requested_by,
            root=root,
            catalog=catalog,
            detail=f"development-unverified:claim:{claim_id}",
            now=now,
        )
    facts = None
    for adapter in adapters.values():
        countries = adapter.capabilities.countries
        if case.jurisdiction not in countries and "*" not in countries:
            continue
        try:
            facts = await adapter.get(case.registration_number, case.jurisdiction, now)
        except Exception:  # noqa: BLE001 - an unreadable source is "not now", never a pass
            facts = None
        if facts is not None and facts.active:
            break
        facts = None
    if facts is None and seen.get("company_basis") == "DOCUMENT_OCR":
        # CR-132: the company check was completed from a stored certificate the platform's OCR read, so there is no register record to name the organization from.
        from zid_platform.modules.organization.ports import establish_document_root

        if tenant.status is TenantStatus.DRAFT:
            await transition(
                conn,
                tenant_id,
                Move.ADVANCE,
                reason_code="ORGANIZATION_SUBMITTED",
                actor=case.requested_by,
                now=now,
            )
        name = (case.legal_name or "Company").strip()[:200] or "Company"
        root = await establish_document_root(
            conn, tenant_id, holder_principal_id=case.requested_by, legal_name=name,
            jurisdiction=case.jurisdiction, identifier=case.registration_number, claim_id=claim_id, now=now,
        )  # fmt: skip
        await set_fields(conn, tenant_id, display_name=name)
        return await _seed_and_advance(
            conn,
            tenant_id,
            principal_id=case.requested_by,
            root=root,
            catalog=catalog,
            detail=f"claim-document:{claim_id}",
            now=now,
        )
    if facts is None:
        raise OnboardingError(
            "SOURCE_UNAVAILABLE",
            "The official source could not be read just now. Nothing was changed; try again.",
        )
    if tenant.status is TenantStatus.DRAFT:
        await transition(
            conn,
            tenant_id,
            Move.ADVANCE,
            reason_code="ORGANIZATION_SUBMITTED",
            actor=case.requested_by,
            now=now,
        )
    root = await establish_claim_root(
        conn,
        tenant_id,
        holder_principal_id=case.requested_by,
        legal_name=facts.legal_name,
        jurisdiction=facts.jurisdiction,
        source_id=facts.adapter_id,
        namespace=facts.registry_authority,
        identifier=facts.registration_number,
        source_revision=facts.response_hash,
        now=now,
    )
    await set_fields(conn, tenant_id, display_name=facts.legal_name)
    return await _seed_and_advance(
        conn,
        tenant_id,
        principal_id=case.requested_by,
        root=root,
        catalog=catalog,
        detail=f"claim:{claim_id}",
        now=now,
    )


log = logging.getLogger("zid.onboarding")


async def auto_establish_pass(
    conn: AsyncConnection, now: datetime, *, catalog: Mapping[str, Mapping[str, Any]]
) -> dict[str, int]:
    """Worker pass: the moment a claim reads as VERIFIED and its company check rests on a stored certificate reading (CR-136), create the organization from it, so nobody has to open a page
    or press a button and nobody at Zoiko iD has to review it. A claim verified from a register record is created by the status page, which has the register adapters. With the LOCAL/TEST
    development shortcut on (ZID_ORG_CLAIM_DEV_AUTOVERIFY, refused anywhere else) every VERIFIED claim is created, as before. A workspace that already has its organization is left alone, and
    one claim that fails is logged and never stops the others. tenant id -> organizations established."""
    from zid_platform.modules.onboarding.application import claims

    shortcut = claims.dev_shortcut_enabled()
    out: dict[str, int] = {}
    for tenant, claim in await claims.kyb_open_claims(conn):  # type: ignore[attr-defined]
        try:
            async with conn.begin_nested(), in_tenant(conn, tenant):
                current = await load_tenant(conn, tenant)
                if current is None or current.organization_id:
                    continue
                seen = await claims.status(conn, tenant, claim, now)
                if seen["outcome"] != "VERIFIED":
                    continue
                if not shortcut and seen.get("company_basis") != "DOCUMENT_OCR":
                    continue
                await establish_from_verified_claim(
                    conn, tenant, claim, adapters={}, catalog=catalog, now=now
                )
            out[tenant] = out.get(tenant, 0) + 1
        except Exception:
            log.exception("could not establish the organization for claim %s", claim)
    return out


async def owner_grant_top_up_pass(
    conn: AsyncConnection, now: datetime, *, catalog: Mapping[str, Mapping[str, Any]]
) -> dict[str, int]:
    """CR-137: every company's active OWNER holds every customer-side action (`owner_actions`), including the ones added to the catalogue after the company was set up. A grant's scope never changes and a
    replacement would orphan the administrators delegated from it, so an owner who lacks some of them is issued ONE more top-level grant with just the missing actions, through the normal issuance gate.
    Repeating it changes nothing once the owner holds them all; one company that fails is logged and never stops the others. tenant id -> grants issued."""
    wanted = frozenset(owner_actions(catalog))
    owners = (
        await conn.execute(
            text(
                "SELECT DISTINCT r.tenant_id, r.principal_id FROM platform.tenant_role_assignment r "
                "JOIN platform.tenant t ON t.tenant_id = r.tenant_id "
                "WHERE r.role = 'OWNER' AND r.status = 'ACTIVE' AND t.organization_id IS NOT NULL"
            )
        )
    ).all()
    out: dict[str, int] = {}
    for tenant_uuid, principal_uuid in owners:
        tenant, principal = str(tenant_uuid), str(principal_uuid)
        try:
            async with conn.begin_nested(), in_tenant(conn, tenant):
                standing = [
                    g
                    for g in await load_grants_for_grantee(conn, principal)
                    if g.tenant_id == tenant
                    and g.status is GrantStatus.ACTIVE
                    and g.grant_type is GrantType.ROLE_ISSUED
                    and g.parent_grant_id is None
                ]
                if not standing or any(g.authority.actions is None for g in standing):
                    continue  # no standing owner grant to extend, or one that already names no limit
                held: set[str] = set().union(*(g.authority.actions for g in standing))  # type: ignore[arg-type]
                missing = wanted - held
                if not missing:
                    continue
                base = max(standing, key=lambda g: g.issued_at)
                await issue_grant(
                    conn,
                    Grant(
                        str(uuid7()), tenant, GrantType.ROLE_ISSUED, principal, principal,
                        base.represented_principal_id,
                        Authority(actions=frozenset(missing), window=Window(now - timedelta(seconds=1), now + ADMIN_GRANT_TERM)),
                        ContinuityMode.PERSON_BOUND, GrantStatus.ACTIVE, now,
                        root_id=base.root_id, delegation_allowed=True, max_delegation_depth=1,
                    ),
                    risk_level=3,
                    now=now,
                )  # fmt: skip
            out[tenant] = out.get(tenant, 0) + 1
        except Exception:
            log.exception("could not top up the owner grants of company %s", tenant)
    return out


async def development_verification(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    principal_id: str,
    legal_name: str,
    catalog: Mapping[str, Mapping[str, Any]],
    environment: str,
    now: datetime | None = None,
) -> OrganizationResult:
    """LOCAL/TEST ONLY: stands in for the registry gate so a freshly signed-up company can be
    administered at once. The synthetic root says what it is (source "development-unverified",
    jurisdiction ZZ) and the call is refused in any other environment; production tenants go
    through `submit_organization` (Companies House / GLEIF)."""
    if environment not in ("local", "test"):
        raise OnboardingError(
            "TENANT_ORGANIZATION_UNVERIFIED", "Development verification is local/test only."
        )
    now = now or datetime.now(UTC)
    await transition(
        conn,
        tenant_id,
        Move.ADVANCE,
        reason_code="ORGANIZATION_SUBMITTED",
        actor=principal_id,
        now=now,
    )
    root = await establish_development_root(
        conn, tenant_id, holder_principal_id=principal_id, legal_name=legal_name, now=now
    )
    return await _seed_and_advance(
        conn,
        tenant_id,
        principal_id=principal_id,
        root=root,
        catalog=catalog,
        detail="development-unverified",
        now=now,
    )
