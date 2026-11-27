"""Make a principal a real tenant administrator for protected actions (ZID-AUTH-001 §24; DEC-B1).

No role shortcut: an organization with a registry root, an authority grant from the root holder to
the admin for the given actions/resources, and an ACTIVE policy release that permits an action
exactly when the authority chain is valid. The PDP then decides as it does in production.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection
from zid_platform.modules.authority.domain.algebra import Authority, Window, scope
from zid_platform.modules.authority.domain.grants import (
    ContinuityMode,
    Grant,
    GrantStatus,
    GrantType,
)
from zid_platform.modules.authority.ports import issue_grant
from zid_platform.modules.organization.domain.roots import (
    Assurance,
    Fact,
    RootAssertion,
    RootStatus,
    RootType,
)
from zid_platform.modules.organization.infrastructure.root_store import (
    create_organization,
    save_root,
)
from zid_platform.modules.policy_authoring.infrastructure import release_store
from zid_platform.modules.policy_authoring.infrastructure.release_store import move
from zid_platform.modules.policy_runtime.domain.release import (
    PolicyCase,
    PolicyRelease,
    ReleaseStatus,
)
from zid_platform.protected import catalog

POLICY = '@id("admin") permit (principal, action, resource) when { context.authority_valid };'
OK = {
    "tenant_match": True,
    "authority_valid": True,
    "authority_stale": False,
    "security_state": "NORMAL",
    "risk_class": 3,
    "purpose": "",
}


def uid() -> str:
    return str(uuid.uuid4())


async def make_admin(
    c: AsyncConnection,
    tenant: str,
    admin: str | list[str],
    actions: list[str],
    resources: list[str],
    *,
    now: datetime | None = None,
    release: bool = True,
    delegable: bool = False,
) -> str:
    """Returns the (first) grant id. Each admin must already exist as an ACTIVE principal.

    `now` shifts the grants' issue time (tests of ordering); `release=False` skips the policy release when one is
    already active for the tenant."""
    admins = [admin] if isinstance(admin, str) else admin
    now = now or datetime.now(UTC)
    org, root, holder = uid(), uid(), uid()
    await create_organization(
        c,
        organization_id=org,
        tenant_id=tenant,
        legal_name="Tenant Org",
        jurisdiction="GB",
        valid_from=now - timedelta(days=400),
    )
    await save_root(
        c,
        tenant,
        RootAssertion(
            root_id=root,
            organization_id=org,
            holder_principal_id=holder,
            root_type=RootType.REGISTRY,
            facts=frozenset({Fact.ENTITY_EXISTENCE, Fact.OFFICIAL_ROLE}),
            assurance=Assurance.AR3,
            status=RootStatus.ACTIVE,
            jurisdiction="GB",
            valid_from=now - timedelta(days=100),
            valid_to=None,
            fresh_until=now + timedelta(days=1),
        ),
    )
    grant_ids = [uid() for _ in admins]
    for grant_id, grantee in zip(grant_ids, admins, strict=True):
        await _grant(
            c, tenant, grant_id, holder, grantee, org, root, actions, resources, now, delegable
        )
    if release:
        await _release(c, tenant, actions, now)
    return grant_ids[0]


async def _grant(
    c: AsyncConnection,
    tenant: str,
    grant_id: str,
    holder: str,
    grantee: str,
    org: str,
    root: str,
    actions: list[str],
    resources: list[str],
    now: datetime,
    delegable: bool = False,
) -> None:
    await issue_grant(
        c,
        Grant(
            grant_id,
            tenant,
            GrantType.ROLE_ISSUED,
            holder,
            grantee,
            org,
            Authority(
                actions=scope(*actions),
                resources=scope(*resources),
                window=Window(now - timedelta(days=1), now + timedelta(days=300)),
            ),
            ContinuityMode.ROLE_CONTINUOUS,
            GrantStatus.ACTIVE,
            now - timedelta(hours=1),
            root_id=root,
            delegation_allowed=delegable,
            max_delegation_depth=1 if delegable else 0,
        ),
        risk_level=3,
        now=now,
    )


async def _release(c: AsyncConnection, tenant: str, actions: list[str], now: datetime) -> None:
    rid = uid()
    await release_store.create_release(
        c,
        PolicyRelease(
            rid,
            tenant,
            POLICY,
            "1",
            ReleaseStatus.DRAFT,
            (
                PolicyCase("ok", "p", actions[0], "r", OK, True),
                PolicyCase("no authority", "p", actions[0], "r", {**OK, "authority_valid": False}),
            ),
            author=uid(),
        ),
        policy_name="admin",
        version=1,
    )
    for target in (ReleaseStatus.VALIDATING, ReleaseStatus.REVIEW):
        await move(c, rid, target, catalog=catalog(), now=now)
    await release_store.add_reviewer(c, rid, uid())
    await move(c, rid, ReleaseStatus.ACTIVE, catalog=catalog(), now=now)


async def give_tenant_role(
    conn: AsyncConnection,
    tenant_id: str,
    principal_id: str,
    role: str = "ADMIN",
    home_cell: str | None = None,
) -> None:
    """A real, ACTIVE tenant role (Owner or Admin) for a principal, as a company administrator holds it.

    `make_admin` above builds the authority a person acts with; this is the other half: the company role. The security review of an agent
    needs both (CR-142 and the Admin-or-Owner rule), so a test reviewer is given both.
    """
    from zid_app import ServiceSettings
    from zid_platform.modules.tenant.domain.lifecycle import Role, RoleOrigin, RoleStatus
    from zid_platform.modules.tenant.infrastructure import tenant_store

    # A role belongs to a tenant record. These worlds use bare tenant ids, so make a minimal active one when there is none.
    await conn.execute(
        text(
            "INSERT INTO platform.tenant (tenant_id, display_name, tenant_slug, status, deployment_mode, home_cell_id, creator_tenant_id, "
            "creator_principal_id, created_at, state_changed_at) VALUES (:t, 'Test company', :s, 'ACTIVE', 'O0', :cell, :t, :p, now(), now()) "
            "ON CONFLICT (tenant_id) DO NOTHING"
        ),
        {
            "t": uuid.UUID(tenant_id),
            "s": f"test-{tenant_id[:8]}",
            "p": uuid.UUID(principal_id),
            "cell": home_cell
            or ServiceSettings.model_fields[
                "cell_id"
            ].default,  # the cell id the app runs as, never the cell UUID
        },
    )
    await tenant_store.insert_role(
        conn,
        tenant_id=tenant_id,
        principal_id=principal_id,
        role=Role(role),
        status=RoleStatus.ACTIVE,
        origin=RoleOrigin.PROVISIONING,
        granted_by=None,
        approved_by=None,
        now=datetime.now(UTC),
    )
