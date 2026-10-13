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


__all__ = ["Anchor", "Resolved", "TrustChainError", "resolve", "trust_anchor"]
