"""Public Protocol interface of the federation module. Other modules import ONLY this."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.federation.application.trust_chain import Anchor, Resolved, resolve
from zid_platform.modules.federation.domain.openid_federation import TrustChainError
from zid_platform.modules.federation.infrastructure.connection_store import load_anchor


async def trust_anchor(conn: AsyncConnection, anchor_id: str) -> Anchor | None:
    row = await load_anchor(conn, anchor_id)
    if row is None:
        return None
    return Anchor(
        anchor_id=str(row["anchor_id"]),
        entity_id=str(row["entity_id"]),
        jwks=row["jwks"],  # type: ignore[arg-type]
        allowed_domains=tuple(row["allowed_domains"]),  # type: ignore[arg-type]
        status=str(row["status"]),
    )


async def set_connection_status(conn: AsyncConnection, connection_id: str, status: str) -> bool:
    """QUARANTINED / ACTIVE / DISABLED for one federation connection (connector framework, G-W2).

    Returns False when there is no such connection in this tenant."""
    from dataclasses import replace as dc_replace

    from zid_platform.modules.federation.infrastructure.connection_store import load, replace

    old = await load(conn, connection_id, lock=True)
    if old is None:
        return False
    if old.status != status:
        new = dc_replace(old, status=status, config_revision=old.config_revision + 1)
        await replace(conn, old, new, status.lower())
    return True


__all__ = [
    "Anchor",
    "Resolved",
    "TrustChainError",
    "resolve",
    "set_connection_status",
    "trust_anchor",
]


async def count_active_connections(conn: AsyncConnection) -> int:
    """Active federation connections of this tenant (RLS scopes it), for plan-limit checks (CR-024)."""
    from sqlalchemy import text

    return int(
        (
            await conn.execute(
                text("SELECT count(*) FROM authn.federation_connection WHERE status = 'ACTIVE'")
            )
        ).scalar_one()
    )
