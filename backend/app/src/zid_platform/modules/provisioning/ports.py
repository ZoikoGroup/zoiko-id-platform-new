"""Public Protocol interface of the provisioning module. Other modules import ONLY this."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncConnection


async def set_source_status(conn: AsyncConnection, source_id: str, status: str) -> bool:
    """ACTIVE / SUSPENDED for one SCIM provisioning source (connector quarantine, G-W2).

    A suspended source can no longer write: its SCIM requests are refused (OVL-23)."""
    from zid_platform.modules.provisioning.infrastructure.scim_store import (
        set_source_status as _set,
    )

    return await _set(conn, source_id, status) is not None


async def count_active_sources(conn: AsyncConnection) -> int:
    """Active SCIM sources of this tenant (RLS scopes it), for plan-limit checks (CR-024)."""
    from sqlalchemy import text

    return int(
        (
            await conn.execute(
                text("SELECT count(*) FROM platform.provisioning_source WHERE status = 'ACTIVE'")
            )
        ).scalar_one()
    )


async def directory_sync_pass(
    conn: AsyncConnection, now: datetime, *, http: object, home_cell: str
) -> dict[str, int]:
    """Worker pass: run every directory sync that is due (each company in its own scope)."""
    from zid_platform.modules.provisioning.application.directory_sync import sync_pass

    return await sync_pass(conn, now, http=http, home_cell=home_cell)  # type: ignore[arg-type]


async def directory_person(conn: AsyncConnection, issuer: str, subject: str) -> str | None:
    """The principal a company's directory sync provisioned for this identity at its provider, or None.

    Matched on the provider's own id (Auth0's `user_id` is the same value as the sign-in `sub`) and on the provider tenant the sync reads, which must be
    the tenant the sign-in connection trusts. Never on an e-mail address. Used inside the company's own scope, at first sign-in, so the person the
    directory provisioned and the person who signs in are one account."""
    from urllib.parse import urlsplit

    from sqlalchemy import text

    host = (urlsplit(issuer).hostname or "").lower()
    if not host or not subject:
        return None
    row = (
        await conn.execute(
            text(
                "SELECT r.principal_id FROM platform.scim_resource r JOIN platform.directory_sync d ON d.source_id = r.source_id "
                "JOIN platform.provisioning_source s ON s.source_id = r.source_id "
                "WHERE r.resource_type = 'User' AND r.deleted_at IS NULL AND r.active AND r.principal_id IS NOT NULL "
                "AND r.external_id = :sub AND d.provider = 'AUTH0' AND lower(d.settings->>'domain') = :host AND s.status = 'ACTIVE' LIMIT 1"
            ),
            {"sub": subject, "host": host},
        )
    ).first()
    return None if row is None else str(row.principal_id)


async def directory_principal(conn: AsyncConnection, external_id: str) -> str | None:
    """The principal the company's directory provisioned under this id of its own (Auth0: `auth0|...`), or None.

    Only a person who is active, in an active source, of the company whose scope the connection is in (RLS), and only when exactly one person has
    that id: an id that names two people is no one the platform can decide for."""
    from sqlalchemy import text

    rows = (
        await conn.execute(
            text(
                "SELECT DISTINCT r.principal_id FROM platform.scim_resource r JOIN platform.provisioning_source s ON s.source_id = r.source_id "
                "WHERE r.resource_type = 'User' AND r.deleted_at IS NULL AND r.active AND r.principal_id IS NOT NULL "
                "AND r.external_id = :x AND s.status = 'ACTIVE' LIMIT 2"
            ),
            {"x": external_id},
        )
    ).all()
    return str(rows[0].principal_id) if len(rows) == 1 else None


__all__ = [
    "count_active_sources",
    "directory_person",
    "directory_principal",
    "directory_sync_pass",
    "set_source_status",
]
