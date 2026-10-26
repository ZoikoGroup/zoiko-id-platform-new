"""Public Protocol interface of the provisioning module. Other modules import ONLY this."""

from __future__ import annotations

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


__all__ = ["count_active_sources", "set_source_status"]
