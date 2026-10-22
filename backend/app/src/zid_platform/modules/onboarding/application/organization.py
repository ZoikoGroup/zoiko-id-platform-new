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

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection
from zid_context import uuid7

from zid_platform.modules.authority.ports import (
    Authority,
    ContinuityMode,
    Grant,
    GrantStatus,
    GrantType,
    Window,
    issue_grant,
)
from zid_platform.modules.onboarding.domain.gates import (
    TENANT_ADMIN_ACTIONS,
    Gate,
    GateStatus,
    OnboardingError,
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


async def _seed_and_advance(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    principal_id: str,
    root: EstablishedRoot,
    catalog: Mapping[str, Mapping[str, Any]],
    detail: str,
    now: datetime,
) -> OrganizationResult:
    """Steps 4-5: baseline release, the holder's PERSON_BOUND tenant-admin grant, OWNER active,
    ORGANIZATION_VERIFYING -> CONFIGURING with the gate report."""
    await set_fields(conn, tenant_id, organization_id=root.organization_id)
    actions = tuple(a for a in TENANT_ADMIN_ACTIONS if a in catalog)
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
        ),
        risk_level=3,
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
