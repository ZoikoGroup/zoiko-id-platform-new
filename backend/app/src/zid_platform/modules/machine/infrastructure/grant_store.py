"""Tool grants issued by the gateway, and the one atomic step that spends one (ZID-GAP-001 WP-11; machine_0007).

Tenant transactions, RLS on. `consume` is a single conditional UPDATE, so two servers presenting the same grant at the same
instant cannot both win: exactly one row comes back.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection


async def save_grant(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    grant_id: str,
    agent_id: str,
    tool_id: str,
    audience: str,
    resource: str,
    args_sha256: str,
    decision_id: str,
    issued_at: datetime,
    expires_at: datetime,
) -> None:
    await conn.execute(
        text(
            "INSERT INTO agent.tool_grant (grant_id, tenant_id, agent_principal_id, tool_id, audience, resource, "
            "args_sha256, decision_id, issued_at, expires_at) VALUES (:g, :t, :a, :tool, :aud, :res, :args, :d, :i, :e)"
        ),
        {
            "g": uuid.UUID(grant_id),
            "t": uuid.UUID(tenant_id),
            "a": uuid.UUID(agent_id),
            "tool": tool_id,
            "aud": uuid.UUID(audience),
            "res": resource,
            "args": args_sha256,
            "d": decision_id,
            "i": issued_at,
            "e": expires_at,
        },
    )


async def load_grant(conn: AsyncConnection, grant_id: str) -> dict[str, Any] | None:
    row = (
        await conn.execute(
            text("SELECT * FROM agent.tool_grant WHERE grant_id = :g"), {"g": uuid.UUID(grant_id)}
        )
    ).first()
    return dict(row._mapping) if row else None


async def consume(
    conn: AsyncConnection, grant_id: str, *, by: str, now: datetime
) -> dict[str, Any] | None:
    """Spend the grant once. None when it is unknown, expired or already spent: the caller says which."""
    row = (
        await conn.execute(
            text(
                "UPDATE agent.tool_grant SET consumed_at = :now, consumed_by = :by "
                "WHERE grant_id = :g AND consumed_at IS NULL AND expires_at > :now RETURNING *"
            ),
            {"g": uuid.UUID(grant_id), "by": uuid.UUID(by), "now": now},
        )
    ).first()
    return dict(row._mapping) if row else None
