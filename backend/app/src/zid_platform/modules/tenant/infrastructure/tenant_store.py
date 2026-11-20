"""PostgreSQL persistence of the tenant record, roles and lifecycle history (G-W0; CR-015).

Everything runs under the caller's RLS tenant scope. The database refuses what the domain refuses
too (OWNER from an invitation, rewritten role rows, shrinking revisions, edits to history), so a
bug above this layer cannot weaken those rules.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Row
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.tenant.domain.lifecycle import (
    Role,
    RoleOrigin,
    RoleStatus,
    TenantError,
    TenantStatus,
    Transition,
)


def _u(value: str | None) -> uuid.UUID | None:
    return uuid.UUID(value) if value else None


def _s(value: Any) -> str | None:
    return str(value) if value is not None else None


@dataclass(frozen=True, slots=True)
class Tenant:
    tenant_id: str
    display_name: str
    tenant_slug: str
    status: TenantStatus
    status_before: TenantStatus | None
    deployment_mode: str
    home_cell_id: str
    organization_id: str | None
    security_profile_id: str
    configuration_revision: int
    security_revision: int
    application_registry_revision: int
    mapping_release_id: str | None
    onboarding_plan_id: str | None
    creator_tenant_id: str
    creator_principal_id: str
    created_at: datetime
    state_changed_at: datetime
    closed_at: datetime | None
    row_version: int
    signup_journey: str | None = None


def _tenant(r: Row[Any]) -> Tenant:
    m = r._mapping
    return Tenant(
        tenant_id=str(m["tenant_id"]),
        display_name=m["display_name"],
        tenant_slug=m["tenant_slug"],
        status=TenantStatus(m["status"]),
        status_before=TenantStatus(m["status_before"]) if m["status_before"] else None,
        deployment_mode=m["deployment_mode"],
        home_cell_id=m["home_cell_id"],
        organization_id=_s(m["organization_id"]),
        security_profile_id=m["security_profile_id"],
        configuration_revision=int(m["configuration_revision"]),
        security_revision=int(m["security_revision"]),
        application_registry_revision=int(m["application_registry_revision"]),
        mapping_release_id=_s(m["mapping_release_id"]),
        onboarding_plan_id=_s(m["onboarding_plan_id"]),
        creator_tenant_id=str(m["creator_tenant_id"]),
        creator_principal_id=str(m["creator_principal_id"]),
        created_at=m["created_at"],
        state_changed_at=m["state_changed_at"],
        closed_at=m["closed_at"],
        row_version=int(m["row_version"]),
        signup_journey=m.get("signup_journey"),
    )


async def insert_tenant(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    display_name: str,
    tenant_slug: str,
    deployment_mode: str,
    home_cell_id: str,
    creator_tenant_id: str,
    creator_principal_id: str,
    now: datetime,
) -> Tenant:
    try:
        row = (
            await conn.execute(
                text("""
                    INSERT INTO platform.tenant (tenant_id, display_name, tenant_slug, status,
                        deployment_mode, home_cell_id, creator_tenant_id, creator_principal_id,
                        created_at, state_changed_at)
                    VALUES (:t, :name, :slug, 'DRAFT', :mode, :cell, :ct, :cp, :now, :now)
                    RETURNING *
                """),
                {
                    "t": uuid.UUID(tenant_id),
                    "name": display_name,
                    "slug": tenant_slug,
                    "mode": deployment_mode,
                    "cell": home_cell_id,
                    "ct": uuid.UUID(creator_tenant_id),
                    "cp": uuid.UUID(creator_principal_id),
                    "now": now,
                },
            )
        ).one()
    except IntegrityError as e:
        if "tenant_slug_unique" in str(e.orig):
            raise TenantError("TENANT_SLUG_TAKEN", "That tenant_slug is already in use.") from e
        raise
    return _tenant(row)


async def load_tenant(
    conn: AsyncConnection, tenant_id: str, *, lock: bool = False
) -> Tenant | None:
    row = (
        await conn.execute(
            text(
                "SELECT * FROM platform.tenant WHERE tenant_id = :t"
                + (" FOR UPDATE" if lock else "")
            ),
            {"t": uuid.UUID(tenant_id)},
        )
    ).first()
    return _tenant(row) if row else None


async def apply_transition(
    conn: AsyncConnection,
    tenant: Tenant,
    t: Transition,
    *,
    reason_code: str,
    actor_type: str,
    actor_principal_id: str | None,
    gate_report: Sequence[Mapping[str, Any]],
    approval_ref: str | None,
    now: datetime,
) -> Tenant:
    """Optimistic on row_version: a concurrent transition of the same tenant loses cleanly."""
    row = (
        await conn.execute(
            text("""
                UPDATE platform.tenant SET status = :to, status_before = :before,
                    state_changed_at = :now,
                    closed_at = CASE WHEN CAST(:to AS text) = 'CLOSED' THEN CAST(:now AS timestamptz) END,
                    row_version = row_version + 1
                WHERE tenant_id = :t AND row_version = :v AND status = :from
                RETURNING *
            """),
            {
                "t": uuid.UUID(tenant.tenant_id),
                "v": tenant.row_version,
                "from": t.from_status.value,
                "to": t.to_status.value,
                "before": t.status_before.value if t.status_before else None,
                "now": now,
            },
        )
    ).first()
    if row is None:
        raise TenantError("CONCURRENT_MODIFICATION", "The tenant changed; reload and retry.")
    await conn.execute(
        text("""
            INSERT INTO platform.tenant_state_transition (transition_id, tenant_id, from_status,
                to_status, move, reason_code, actor_type, actor_principal_id, gate_report,
                approval_ref, occurred_at)
            VALUES (:id, :t, :from, :to, :move, :reason, :atype, :actor, CAST(:gates AS jsonb),
                :approval, :now)
        """),
        {
            "id": uuid.uuid4(),
            "t": uuid.UUID(tenant.tenant_id),
            "from": t.from_status.value,
            "to": t.to_status.value,
            "move": t.move.value,
            "reason": reason_code,
            "atype": actor_type,
            "actor": _u(actor_principal_id),
            "gates": json.dumps(list(gate_report), default=str),
            "approval": approval_ref,
            "now": now,
        },
    )
    return _tenant(row)


async def record_creation(
    conn: AsyncConnection, tenant: Tenant, *, actor_principal_id: str, now: datetime
) -> None:
    await conn.execute(
        text("""
            INSERT INTO platform.tenant_state_transition (transition_id, tenant_id, from_status,
                to_status, move, reason_code, actor_type, actor_principal_id, occurred_at)
            VALUES (:id, :t, NULL, 'DRAFT', 'CREATE', 'BOOTSTRAP', 'HUMAN', :actor, :now)
        """),
        {
            "id": uuid.uuid4(),
            "t": uuid.UUID(tenant.tenant_id),
            "actor": uuid.UUID(actor_principal_id),
            "now": now,
        },
    )


async def transitions(conn: AsyncConnection, limit: int = 100) -> list[dict[str, Any]]:
    rows = (
        await conn.execute(
            text(
                "SELECT * FROM platform.tenant_state_transition ORDER BY occurred_at DESC LIMIT :n"
            ),
            {"n": limit},
        )
    ).all()
    return [dict(r._mapping) for r in rows]


_UUID_FIELDS = frozenset({"organization_id", "mapping_release_id", "onboarding_plan_id"})
_TEXT_FIELDS = frozenset(
    {
        "display_name",
        "tenant_slug",
        "deployment_mode",
        "security_profile_id",
        "home_cell_id",
        "signup_journey",
    }
)


async def set_fields(conn: AsyncConnection, tenant_id: str, **fields: Any) -> Tenant:
    """Update configuration fields (never status, revisions or identity) and bump row_version."""
    assert fields and set(fields) <= _UUID_FIELDS | _TEXT_FIELDS, set(fields)
    assignments = ", ".join(f"{k} = :{k}" for k in fields)
    values = {k: (_u(v) if k in _UUID_FIELDS else v) for k, v in fields.items()}
    row = (
        await conn.execute(
            text(
                f"UPDATE platform.tenant SET {assignments}, row_version = row_version + 1 "
                "WHERE tenant_id = :t RETURNING *"
            ),
            {**values, "t": uuid.UUID(tenant_id)},
        )
    ).first()
    if row is None:
        raise TenantError("TENANT_NOT_FOUND")
    if (
        "display_name" in fields
    ):  # the sign-in directory shows the name people know the workspace by
        await conn.execute(
            text("UPDATE platform.workspace_directory SET display_name = :n WHERE tenant_id = :t"),
            {"n": fields["display_name"], "t": uuid.UUID(tenant_id)},
        )
    return _tenant(row)


async def bump_configuration(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    change_kind: str,
    change_commitment: str,
    actor_principal_id: str | None,
    approval_ref: str | None,
    now: datetime,
    security: bool = False,
    applications: bool = False,
) -> int:
    """configuration_revision += 1 (and security_revision/application_registry_revision where the
    change is security-significant or touches the application registry); history row appended."""
    row = (
        await conn.execute(
            text("""
                UPDATE platform.tenant SET configuration_revision = configuration_revision + 1,
                    security_revision = security_revision + :sec,
                    application_registry_revision = application_registry_revision + :apps,
                    row_version = row_version + 1
                WHERE tenant_id = :t RETURNING configuration_revision
            """),
            {"t": uuid.UUID(tenant_id), "sec": int(security), "apps": int(applications)},
        )
    ).first()
    if row is None:
        raise TenantError("TENANT_NOT_FOUND")
    revision = int(row.configuration_revision)
    await conn.execute(
        text("""
            INSERT INTO platform.tenant_configuration_revision (tenant_id, revision, change_kind,
                change_commitment, actor_principal_id, approval_ref, created_at)
            VALUES (:t, :r, :kind, :c, :actor, :approval, :now)
        """),
        {
            "t": uuid.UUID(tenant_id),
            "r": revision,
            "kind": change_kind,
            "c": change_commitment,
            "actor": _u(actor_principal_id),
            "approval": approval_ref,
            "now": now,
        },
    )
    return revision


async def configuration_history(conn: AsyncConnection, limit: int = 100) -> list[dict[str, Any]]:
    rows = (
        await conn.execute(
            text(
                "SELECT * FROM platform.tenant_configuration_revision ORDER BY revision DESC "
                "LIMIT :n"
            ),
            {"n": limit},
        )
    ).all()
    return [dict(r._mapping) for r in rows]


# ------------------------------------------------------------------------------------- roles


@dataclass(frozen=True, slots=True)
class RoleAssignment:
    assignment_id: str
    tenant_id: str
    principal_id: str
    role: Role
    status: RoleStatus
    origin: RoleOrigin
    granted_by: str | None
    approved_by: str | None
    created_at: datetime
    activated_at: datetime | None


def _role(r: Row[Any]) -> RoleAssignment:
    m = r._mapping
    return RoleAssignment(
        str(m["assignment_id"]),
        str(m["tenant_id"]),
        str(m["principal_id"]),
        Role(m["role"]),
        RoleStatus(m["status"]),
        RoleOrigin(m["origin"]),
        _s(m["granted_by"]),
        _s(m["approved_by"]),
        m["created_at"],
        m["activated_at"],
    )


async def insert_role(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    principal_id: str,
    role: Role,
    status: RoleStatus,
    origin: RoleOrigin,
    granted_by: str | None,
    approved_by: str | None,
    now: datetime,
) -> RoleAssignment:
    try:
        row = (
            await conn.execute(
                text("""
                    INSERT INTO platform.tenant_role_assignment (assignment_id, tenant_id,
                        principal_id, role, status, origin, granted_by, approved_by, created_at,
                        activated_at)
                    VALUES (:id, :t, :p, :role, :status, :origin, :by, :approved, :now,
                        CASE WHEN :status = 'ACTIVE' THEN CAST(:now AS timestamptz) END)
                    RETURNING *
                """),
                {
                    "id": uuid.uuid4(),
                    "t": uuid.UUID(tenant_id),
                    "p": uuid.UUID(principal_id),
                    "role": role.value,
                    "status": status.value,
                    "origin": origin.value,
                    "by": _u(granted_by),
                    "approved": _u(approved_by),
                    "now": now,
                },
            )
        ).one()
    except IntegrityError as e:
        if "tenant_role_live" in str(e.orig):
            raise TenantError("TENANT_ROLE_EXISTS", f"The principal already holds {role}.") from e
        raise
    return _role(row)


async def roles(
    conn: AsyncConnection, *, principal_id: str | None = None, live_only: bool = True
) -> list[RoleAssignment]:
    # The company in scope is named explicitly (the same test as the row-level security policy), so a connection that bypasses row-level security (the worker) can never read or change another
    # company's roles: CR-136 found the worker activating the registration tenant's OWNER instead of the claimant's.
    where = ["tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid"]
    params: dict[str, Any] = {}
    if principal_id:
        where.append("principal_id = :p")
        params["p"] = uuid.UUID(principal_id)
    if live_only:
        where.append("status <> 'REVOKED'")
    rows = (
        await conn.execute(
            text(
                "SELECT * FROM platform.tenant_role_assignment WHERE "
                + " AND ".join(where)
                + " ORDER BY created_at"
            ),
            params,
        )
    ).all()
    return [_role(r) for r in rows]


async def active_owner_count(conn: AsyncConnection, *, lock: bool = False) -> int:
    if lock:  # serialize concurrent owner removals of the same tenant
        await conn.execute(
            text(
                "SELECT 1 FROM platform.tenant WHERE tenant_id = "
                "nullif(current_setting('app.tenant_id', true), '')::uuid FOR UPDATE"
            )
        )
    return int(
        (
            await conn.execute(
                text(
                    "SELECT count(*) FROM platform.tenant_role_assignment "
                    "WHERE role = 'OWNER' AND status = 'ACTIVE' AND tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid"
                )
            )
        ).scalar_one()
    )


async def set_role_status(
    conn: AsyncConnection,
    assignment_id: str,
    *,
    expect: Sequence[RoleStatus],
    status: RoleStatus,
    actor: str | None,
    now: datetime,
) -> RoleAssignment | None:
    row = (
        await conn.execute(
            text("""
                UPDATE platform.tenant_role_assignment SET status = :s,
                    activated_at = CASE WHEN :s = 'ACTIVE' THEN CAST(:now AS timestamptz)
                                        ELSE activated_at END,
                    revoked_at = CASE WHEN :s = 'REVOKED' THEN CAST(:now AS timestamptz) END,
                    revoked_by = CASE WHEN :s = 'REVOKED' THEN CAST(:actor AS uuid) END
                WHERE assignment_id = :id AND status = ANY(:expect)
                RETURNING *
            """),
            {
                "id": uuid.UUID(assignment_id),
                "s": status.value,
                "expect": [s.value for s in expect],
                "actor": actor,
                "now": now,
            },
        )
    ).first()
    return _role(row) if row else None


# ------------------------------------------------------------------------- bootstrap requests


async def open_bootstraps(conn: AsyncConnection, creator_principal_id: str) -> int:
    """Open drafts of this creator, counted under the creating tenant's own RLS scope."""
    return int(
        (
            await conn.execute(
                text(
                    "SELECT count(*) FROM platform.tenant_bootstrap_request "
                    "WHERE creator_principal_id = :p AND status = 'OPEN'"
                ),
                {"p": uuid.UUID(creator_principal_id)},
            )
        ).scalar_one()
    )


async def insert_bootstrap(
    conn: AsyncConnection,
    *,
    creating_tenant_id: str,
    creator_principal_id: str,
    new_tenant_id: str,
    now: datetime,
) -> None:
    await conn.execute(
        text("""
            INSERT INTO platform.tenant_bootstrap_request (request_id, tenant_id,
                creator_principal_id, new_tenant_id, created_at)
            VALUES (:id, :t, :p, :n, :now)
        """),
        {
            "id": uuid.uuid4(),
            "t": uuid.UUID(creating_tenant_id),
            "p": uuid.UUID(creator_principal_id),
            "n": uuid.UUID(new_tenant_id),
            "now": now,
        },
    )


async def close_bootstrap(conn: AsyncConnection, new_tenant_id: str, status: str) -> None:
    await conn.execute(
        text(
            "UPDATE platform.tenant_bootstrap_request SET status = :s "
            "WHERE new_tenant_id = :n AND status = 'OPEN'"
        ),
        {"n": uuid.UUID(new_tenant_id), "s": status},
    )


# ------------------------------------------------------------------- workspace directory (no RLS)


async def insert_workspace(
    conn: AsyncConnection, *, workspace: str, tenant_id: str, client_id: str, name: str
) -> None:
    try:
        async with conn.begin_nested():
            await conn.execute(
                text(
                    "INSERT INTO platform.workspace_directory (workspace, tenant_id, "
                    "console_client_id, display_name) VALUES (:w, :t, :c, :n)"
                ),
                {"w": workspace, "t": uuid.UUID(tenant_id), "c": client_id, "n": name},
            )
    except IntegrityError as e:
        raise TenantError("TENANT_SLUG_TAKEN", "That workspace name is already in use.") from e


async def workspace(conn: AsyncConnection, name: str) -> dict[str, str] | None:
    """Workspace name -> (tenant, console client, display name); the pre-tenant lookup."""
    row = (
        await conn.execute(
            text(
                "SELECT workspace, tenant_id, console_client_id, display_name "
                "FROM platform.workspace_directory WHERE workspace = :w"
            ),
            {"w": name},
        )
    ).first()
    if row is None:
        return None
    return {
        "workspace": row.workspace,
        "tenant_id": str(row.tenant_id),
        "console_client_id": row.console_client_id,
        "display_name": row.display_name,
    }


async def console_client_for(conn: AsyncConnection, tenant_id: str) -> str | None:
    """The opaque first-party console client of a tenant (what a sign-in link needs), never its name."""
    row = (
        await conn.execute(
            text("SELECT console_client_id FROM platform.workspace_directory WHERE tenant_id = :t"),
            {"t": uuid.UUID(tenant_id)},
        )
    ).first()
    return None if row is None else str(row.console_client_id)


async def link_person(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    principal_id: str,
    person_id: str,
    source: str,
    evidence_ref: str | None = None,
    linked_by: str | None = None,
) -> bool:
    """Record that an account belongs to a person. Append-only: a second link for the same account changes nothing."""
    row = await conn.execute(
        text("""
            INSERT INTO platform.principal_person_link
                (tenant_id, principal_id, person_id, source, evidence_ref, linked_by)
            VALUES (:t, :p, :person, :source, :ref, :by)
            ON CONFLICT (tenant_id, principal_id) DO NOTHING
            RETURNING principal_id
        """),
        {
            "t": uuid.UUID(tenant_id),
            "p": uuid.UUID(principal_id),
            "person": uuid.UUID(person_id),
            "source": source,
            "ref": evidence_ref,
            "by": _u(linked_by),
        },
    )
    return row.first() is not None


async def person_of(conn: AsyncConnection, principal_id: str) -> str:
    """The natural person behind an account: the linked person, else the account itself (one account is one person until a link
    says otherwise)."""
    row = (
        await conn.execute(
            text("SELECT person_id FROM platform.principal_person_link WHERE principal_id = :p"),
            {"p": uuid.UUID(principal_id)},
        )
    ).first()
    return str(row.person_id) if row else principal_id
