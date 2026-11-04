"""Billing grants: read, give, revoke (ZID-COM-CL-002; slice L-14). The row never changes except to be revoked once."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.tenant.domain.billing_access import Bundle, Grant


def _grant(r: Any) -> Grant:
    return Grant(
        grant_id=str(r.grant_id),
        grantee_principal_id=str(r.grantee_principal_id),
        bundle=Bundle(r.bundle),
        permissions=frozenset(r.permissions),
        expires_at=r.expires_at,
        revoked_at=r.revoked_at,
    )


async def grants_of(conn: AsyncConnection, principal_id: str) -> tuple[Grant, ...]:
    rows = (
        await conn.execute(
            text(
                "SELECT * FROM platform.billing_grant WHERE grantee_principal_id = :p ORDER BY created_at"
            ),
            {"p": uuid.UUID(principal_id)},
        )
    ).all()
    return tuple(_grant(r) for r in rows)


async def list_grants(conn: AsyncConnection) -> list[dict[str, object]]:
    rows = (
        await conn.execute(text("SELECT * FROM platform.billing_grant ORDER BY created_at DESC"))
    ).all()
    return [
        {
            "grant_id": str(r.grant_id),
            "grantee_principal_id": str(r.grantee_principal_id),
            "bundle": r.bundle,
            "permissions": sorted(r.permissions),
            "reason": r.reason,
            "granted_by": str(r.granted_by),
            "expires_at": r.expires_at.isoformat() if r.expires_at else None,
            "created_at": r.created_at.isoformat(),
            "revoked_at": r.revoked_at.isoformat() if r.revoked_at else None,
        }
        for r in rows
    ]


async def insert_grant(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    grantee_principal_id: str,
    grantee_person_id: str,
    bundle: Bundle,
    permissions: frozenset[str],
    reason: str,
    granted_by: str,
    expires_at: datetime | None,
) -> str:
    grant_id = uuid.uuid4()
    await conn.execute(
        text("""
            INSERT INTO platform.billing_grant (grant_id, tenant_id, grantee_principal_id, grantee_person_id, bundle,
                permissions, reason, granted_by, expires_at)
            VALUES (:g, :t, :gp, :gperson, :b, :perms, :reason, :by, :exp)
        """),
        {
            "g": grant_id,
            "t": uuid.UUID(tenant_id),
            "gp": uuid.UUID(grantee_principal_id),
            "gperson": uuid.UUID(grantee_person_id),
            "b": bundle.value,
            "perms": sorted(permissions),
            "reason": reason,
            "by": uuid.UUID(granted_by),
            "exp": expires_at,
        },
    )
    return str(grant_id)


async def revoke_grant(
    conn: AsyncConnection, grant_id: str, *, revoked_by: str, now: datetime
) -> str | None:
    """The grantee's principal, or None when there is no live grant by that id."""
    row = (
        await conn.execute(
            text("""
                UPDATE platform.billing_grant SET revoked_at = :now, revoked_by = :by
                WHERE grant_id = :g AND revoked_at IS NULL
                RETURNING grantee_principal_id
            """),
            {"g": uuid.UUID(grant_id), "by": uuid.UUID(revoked_by), "now": now},
        )
    ).first()
    return str(row.grantee_principal_id) if row else None
