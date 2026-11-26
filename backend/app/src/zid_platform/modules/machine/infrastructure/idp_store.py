"""SQL for outside identity provider bindings (CR-169; machine_0015). Tenant transactions with RLS on; nothing here decides anything."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

_COLS = (
    "binding_id, agent_principal_id, profile, issuer, subject, audience, environment, keys, algs, keys_fingerprint, status, external_claims, claims_seen_at, last_ok_at, last_failure, "
    "created_by, created_at, revoked_by, revoked_at, expires_at"
)


def _row(x: Any) -> dict[str, Any]:
    return dict(x._mapping)


async def insert(
    conn: AsyncConnection, tenant_id: str, *, binding_id: str, agent_id: str, profile: str, issuer: str, subject: str, audience: str, environment: str, keys: list[dict[str, Any]], algs: list[str],
    fingerprint: str, created_by: str, expires_at: datetime | None,
) -> bool:  # fmt: skip
    """False when this issuer and subject already has a live binding in this environment."""
    res = await conn.execute(
        text(
            "INSERT INTO agent.idp_binding (binding_id, tenant_id, agent_principal_id, profile, issuer, subject, audience, environment, keys, algs, keys_fingerprint, created_by, expires_at) "
            "VALUES (:b, :t, :a, :p, :i, :s, :au, :e, CAST(:k AS jsonb), :al, :f, :c, :x) ON CONFLICT DO NOTHING"
        ),
        {
            "b": uuid.UUID(binding_id), "t": uuid.UUID(tenant_id), "a": uuid.UUID(agent_id), "p": profile, "i": issuer, "s": subject, "au": audience, "e": environment, "k": json.dumps(keys), "al": algs,
            "f": fingerprint, "c": uuid.UUID(created_by), "x": expires_at,
        },
    )  # fmt: skip
    return bool(res.rowcount)


async def get(conn: AsyncConnection, binding_id: str, *, lock: bool = False) -> dict[str, Any] | None:
    try:
        key = uuid.UUID(binding_id)
    except ValueError:
        return None
    row = (await conn.execute(text(f"SELECT {_COLS} FROM agent.idp_binding WHERE binding_id = :b{' FOR UPDATE' if lock else ''}"), {"b": key})).first()
    return _row(row) if row else None


async def for_agent(conn: AsyncConnection, agent_id: str) -> list[dict[str, Any]]:
    rows = await conn.execute(text(f"SELECT {_COLS} FROM agent.idp_binding WHERE agent_principal_id = :a ORDER BY created_at DESC"), {"a": uuid.UUID(agent_id)})
    return [_row(x) for x in rows]


async def list_all(conn: AsyncConnection, *, limit: int) -> list[dict[str, Any]]:
    rows = await conn.execute(text(f"SELECT {_COLS} FROM agent.idp_binding ORDER BY created_at DESC LIMIT :n"), {"n": limit})
    return [_row(x) for x in rows]


async def take_proof(conn: AsyncConnection, tenant_id: str, binding_id: str, jti_digest: str, now: datetime) -> bool:
    """One token proves a binding once. False when this token id was already used."""
    res = await conn.execute(
        text("INSERT INTO agent.idp_proof (binding_id, tenant_id, jti_digest, accepted_at) VALUES (:b, :t, :j, :n) ON CONFLICT DO NOTHING"),
        {"b": uuid.UUID(binding_id), "t": uuid.UUID(tenant_id), "j": jti_digest, "n": now},
    )
    return bool(res.rowcount)


async def record_ok(conn: AsyncConnection, binding_id: str, claims: dict[str, str], now: datetime) -> None:
    await conn.execute(
        text("UPDATE agent.idp_binding SET last_ok_at = :n, last_failure = NULL, external_claims = CAST(:c AS jsonb), claims_seen_at = :n WHERE binding_id = :b"),
        {"n": now, "c": json.dumps(claims), "b": uuid.UUID(binding_id)},
    )


async def record_failure(conn: AsyncConnection, binding_id: str, code: str) -> None:
    await conn.execute(text("UPDATE agent.idp_binding SET last_failure = :c WHERE binding_id = :b"), {"c": code[:100], "b": uuid.UUID(binding_id)})


async def revoke(conn: AsyncConnection, binding_id: str, *, by: str, now: datetime) -> bool:
    res = await conn.execute(
        text("UPDATE agent.idp_binding SET status = 'REVOKED', revoked_by = :u, revoked_at = :n WHERE binding_id = :b AND status = 'ACTIVE'"), {"u": uuid.UUID(by), "n": now, "b": uuid.UUID(binding_id)}
    )
    return bool(res.rowcount)


async def active_bindings(conn: AsyncConnection, *, limit: int) -> list[dict[str, Any]]:
    rows = await conn.execute(text(f"SELECT {_COLS} FROM agent.idp_binding WHERE status = 'ACTIVE' ORDER BY created_at DESC LIMIT :n"), {"n": limit})
    return [_row(x) for x in rows]
