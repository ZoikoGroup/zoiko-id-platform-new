"""Setup acknowledgements and activation records in PostgreSQL (slice L-13, CR-049). Every function runs in the caller's tenant transaction."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection


@dataclass(frozen=True, slots=True)
class Ack:
    kind: str
    version: str
    actor_principal_id: str
    locale: str
    acknowledged_at: datetime


async def acknowledgements(conn: AsyncConnection) -> list[Ack]:
    rows = (
        await conn.execute(
            text(
                "SELECT kind, version, actor_principal_id, locale, acknowledged_at "
                "FROM platform.organization_acknowledgement ORDER BY acknowledged_at"
            )
        )
    ).all()
    return [
        Ack(r.kind, r.version, str(r.actor_principal_id), r.locale, r.acknowledged_at) for r in rows
    ]


async def insert_ack(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    kind: str,
    version: str,
    actor: str,
    locale: str,
    idempotency_key: str | None,
    now: datetime,
) -> bool:
    """False if this organization already accepted this text at this version: the retry is the same acceptance, not a second one."""
    row = await conn.execute(
        text("""
            INSERT INTO platform.organization_acknowledgement (ack_id, tenant_id, kind, version, actor_principal_id, locale,
                idempotency_key, acknowledged_at)
            VALUES (:id, :t, :k, :v, :a, :l, :idem, :now) ON CONFLICT (tenant_id, kind, version) DO NOTHING RETURNING ack_id
        """),
        {
            "id": uuid.uuid4(),
            "t": uuid.UUID(tenant_id),
            "k": kind,
            "v": version,
            "a": uuid.UUID(actor),
            "l": locale,
            "idem": idempotency_key,
            "now": now,
        },
    )
    return row.first() is not None


async def activation(conn: AsyncConnection) -> dict[str, Any] | None:
    row = (
        await conn.execute(
            text(
                "SELECT activated_by, gate_report, policy_versions, activated_at FROM platform.organization_activation"
            )
        )
    ).first()
    if row is None:
        return None
    return {
        "activated_by": str(row.activated_by),
        "gate_report": row.gate_report,
        "policy_versions": row.policy_versions,
        "activated_at": row.activated_at,
    }


async def insert_activation(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    actor: str,
    gate_report: dict[str, Any],
    policy_versions: dict[str, Any],
    now: datetime,
) -> bool:
    row = await conn.execute(
        text("""
            INSERT INTO platform.organization_activation (tenant_id, activated_by, gate_report, policy_versions, activated_at)
            VALUES (:t, :a, CAST(:g AS jsonb), CAST(:p AS jsonb), :now) ON CONFLICT (tenant_id) DO NOTHING RETURNING tenant_id
        """),
        {
            "t": uuid.UUID(tenant_id),
            "a": uuid.UUID(actor),
            "g": json.dumps(gate_report, sort_keys=True),
            "p": json.dumps(policy_versions, sort_keys=True),
            "now": now,
        },
    )
    return row.first() is not None
