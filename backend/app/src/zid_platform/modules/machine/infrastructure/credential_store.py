"""SQL for agent credentials and their presentations (CR-169; machine_0016). Tenant transactions with RLS on; nothing here decides anything."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

_COLS = "credential_id, agent_principal_id, jkt, public_key, audience, mandate_id, mandate_hash, evidence_class, token, status, issued_by, issued_at, expires_at, revoked_by, revoked_at"


def _row(x: Any) -> dict[str, Any]:
    return dict(x._mapping)


async def insert(
    conn: AsyncConnection, tenant_id: str, *, credential_id: str, agent_id: str, jkt: str, public_key: dict[str, Any], audience: str | None, mandate_id: str | None, mandate_hash: str | None,
    evidence_class: str, token: str, issued_by: str, issued_at: datetime, expires_at: datetime,
) -> None:  # fmt: skip
    await conn.execute(
        text(
            "INSERT INTO agent.agent_credential (credential_id, tenant_id, agent_principal_id, jkt, public_key, audience, mandate_id, mandate_hash, evidence_class, token, issued_by, issued_at, expires_at) "
            "VALUES (:c, :t, :a, :j, CAST(:k AS jsonb), :au, :m, :mh, :e, :tok, :by, :ia, :ex)"
        ),
        {
            "c": uuid.UUID(credential_id), "t": uuid.UUID(tenant_id), "a": uuid.UUID(agent_id), "j": jkt, "k": json.dumps(public_key), "au": audience, "m": uuid.UUID(mandate_id) if mandate_id else None,
            "mh": mandate_hash, "e": evidence_class, "tok": token, "by": uuid.UUID(issued_by), "ia": issued_at, "ex": expires_at,
        },
    )  # fmt: skip


async def get(conn: AsyncConnection, credential_id: str, *, lock: bool = False) -> dict[str, Any] | None:
    try:
        key = uuid.UUID(credential_id)
    except ValueError:
        return None
    row = (await conn.execute(text(f"SELECT {_COLS} FROM agent.agent_credential WHERE credential_id = :c{' FOR UPDATE' if lock else ''}"), {"c": key})).first()
    return _row(row) if row else None


async def for_agent(conn: AsyncConnection, agent_id: str | None, *, limit: int) -> list[dict[str, Any]]:
    rows = await conn.execute(
        text(f"SELECT {_COLS} FROM agent.agent_credential WHERE (CAST(:a AS uuid) IS NULL OR agent_principal_id = CAST(:a AS uuid)) ORDER BY issued_at DESC LIMIT :n"), {"a": agent_id, "n": limit}
    )
    return [_row(x) for x in rows]


async def revoke(conn: AsyncConnection, credential_id: str, *, by: str, now: datetime) -> bool:
    res = await conn.execute(
        text("UPDATE agent.agent_credential SET status = 'REVOKED', revoked_by = :b, revoked_at = :n WHERE credential_id = :c AND status = 'ACTIVE'"),
        {"b": uuid.UUID(by), "n": now, "c": uuid.UUID(credential_id)},
    )
    return bool(res.rowcount)


async def revoke_all_for_agent(conn: AsyncConnection, agent_id: str, *, by: str, now: datetime) -> int:
    res = await conn.execute(
        text("UPDATE agent.agent_credential SET status = 'REVOKED', revoked_by = :b, revoked_at = :n WHERE agent_principal_id = :a AND status = 'ACTIVE'"),
        {"b": uuid.UUID(by), "n": now, "a": uuid.UUID(agent_id)},
    )
    return int(res.rowcount or 0)


async def take_presentation(conn: AsyncConnection, tenant_id: str, credential_id: str, jti_digest: str, relying_party: str, now: datetime) -> bool:
    """One presentation proves possession once. False when this presentation id was already used."""
    res = await conn.execute(
        text("INSERT INTO agent.credential_presentation (credential_id, tenant_id, jti_digest, relying_party, accepted_at) VALUES (:c, :t, :j, :r, :n) ON CONFLICT DO NOTHING"),
        {"c": uuid.UUID(credential_id), "t": uuid.UUID(tenant_id), "j": jti_digest, "r": relying_party[:300], "n": now},
    )
    return bool(res.rowcount)
