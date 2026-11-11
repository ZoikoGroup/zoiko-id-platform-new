from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.tenant.domain.custom_roles import CustomRoleError


def _view(r: Any, members: int | None = None) -> dict[str, Any]:
    out = {
        "role_id": str(r.role_id),
        "name": r.name,
        "description": r.description,
        "base_role": r.base_role,
        "kind": r.kind,
        "actions": list(r.actions),
        "status": r.status,
        "created_at": r.created_at.isoformat(),
    }
    if members is not None:
        out["members"] = members
    return out


async def create(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    name: str,
    base_role: str,
    actions: list[str],
    description: str,
    actor: str,
    kind: str = "NARROW",
) -> dict[str, Any]:
    rid = uuid.uuid4()
    try:
        async with conn.begin_nested():
            r = (
                await conn.execute(
                    text(
                        "INSERT INTO platform.custom_role (role_id, tenant_id, name, description, base_role, actions, created_by, kind) "
                        "VALUES (:id, :t, :n, :d, :b, :a, :u, :k) RETURNING *"
                    ),
                    {
                        "id": rid,
                        "t": uuid.UUID(tenant_id),
                        "n": name,
                        "d": description,
                        "b": base_role,
                        "a": actions,
                        "u": uuid.UUID(actor),
                        "k": kind,
                    },
                )
            ).one()
    except IntegrityError as e:
        raise CustomRoleError("CUSTOM_ROLE_EXISTS", "A role with that name already exists.") from e
    return _view(r, 0)


async def listing(conn: AsyncConnection) -> list[dict[str, Any]]:
    rows = (
        await conn.execute(
            text(
                "SELECT r.*, (SELECT count(*) FROM platform.custom_role_assignment a WHERE a.role_id = r.role_id AND a.revoked_at IS NULL) AS members "
                "FROM platform.custom_role r ORDER BY r.status, lower(r.name)"
            )
        )
    ).all()
    return [_view(r, r.members) for r in rows]


async def load(conn: AsyncConnection, role_id: str) -> Any:
    r = (
        await conn.execute(
            text("SELECT * FROM platform.custom_role WHERE role_id = :id"),
            {"id": uuid.UUID(role_id)},
        )
    ).first()
    if r is None:
        raise CustomRoleError("CUSTOM_ROLE_NOT_FOUND", "No such custom role.")
    return r


async def retire(conn: AsyncConnection, role_id: str) -> None:
    await load(conn, role_id)
    await conn.execute(
        text(
            "UPDATE platform.custom_role SET status = 'RETIRED', retired_at = now() WHERE role_id = :id AND status = 'ACTIVE'"
        ),
        {"id": uuid.UUID(role_id)},
    )
    await conn.execute(
        text(
            "UPDATE platform.custom_role_assignment SET revoked_at = now() WHERE role_id = :id AND revoked_at IS NULL"
        ),
        {"id": uuid.UUID(role_id)},
    )


async def assign(
    conn: AsyncConnection, tenant_id: str, role_id: str, principal_id: str, actor: str
) -> None:
    await conn.execute(
        text(
            "INSERT INTO platform.custom_role_assignment (role_id, principal_id, tenant_id, assigned_by) VALUES (:r, :p, :t, :a) "
            "ON CONFLICT (role_id, principal_id) DO UPDATE SET revoked_at = NULL, assigned_at = now(), assigned_by = :a"
        ),
        {
            "r": uuid.UUID(role_id),
            "p": uuid.UUID(principal_id),
            "t": uuid.UUID(tenant_id),
            "a": uuid.UUID(actor),
        },
    )


async def unassign(conn: AsyncConnection, role_id: str, principal_id: str) -> bool:
    res = await conn.execute(
        text(
            "UPDATE platform.custom_role_assignment SET revoked_at = now() WHERE role_id = :r AND principal_id = :p AND revoked_at IS NULL"
        ),
        {"r": uuid.UUID(role_id), "p": uuid.UUID(principal_id)},
    )
    return res.rowcount == 1


async def members(conn: AsyncConnection, role_id: str) -> list[dict[str, str]]:
    rows = (
        await conn.execute(
            text(
                "SELECT principal_id, assigned_at FROM platform.custom_role_assignment WHERE role_id = :r AND revoked_at IS NULL ORDER BY assigned_at"
            ),
            {"r": uuid.UUID(role_id)},
        )
    ).all()
    return [
        {"principal_id": str(r.principal_id), "assigned_at": r.assigned_at.isoformat()}
        for r in rows
    ]


async def held_extensions(conn: AsyncConnection, principal_id: str) -> frozenset[str]:
    """The extra actions the person's live EXTEND roles add (empty when they hold none)."""
    rows = (
        await conn.execute(
            text(
                "SELECT unnest(r.actions) AS a FROM platform.custom_role_assignment x JOIN platform.custom_role r USING (role_id) "
                "WHERE x.principal_id = :p AND x.revoked_at IS NULL AND r.status = 'ACTIVE' AND r.kind = 'EXTEND'"
            ),
            {"p": uuid.UUID(principal_id)},
        )
    ).all()
    return frozenset(r.a for r in rows)


async def held_actions(conn: AsyncConnection, principal_id: str) -> list[list[str]]:
    """The action lists of the live NARROW custom roles a person holds (empty when they hold none)."""
    rows = (
        await conn.execute(
            text(
                "SELECT r.actions FROM platform.custom_role_assignment a JOIN platform.custom_role r USING (role_id) "
                "WHERE a.principal_id = :p AND a.revoked_at IS NULL AND r.status = 'ACTIVE' AND r.kind = 'NARROW'"
            ),
            {"p": uuid.UUID(principal_id)},
        )
    ).all()
    return [list(r.actions) for r in rows]
