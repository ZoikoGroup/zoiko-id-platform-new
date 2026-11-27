"""Pointers to the photographs kept for a partner's customer, and the append-only record of each time one was opened (migration verify_api_0020).
Rows only: the images themselves are encrypted objects in the evidence vault. Rows are confined to the company by row-level security."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

_COLS = "media_id, session_id, kind, attempt, vault_object_id, content_type, byte_length, captured_at, expires_at, created_at"


async def next_attempt(conn: AsyncConnection, session_id: str) -> int:
    got = await conn.execute(
        text(
            "SELECT coalesce(max(attempt), 0) + 1 FROM verify.api_session_media WHERE session_id = :s"
        ),
        {"s": session_id},
    )
    return int(got.scalar_one())


async def add(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    session_id: str,
    kind: str,
    attempt: int,
    vault_object_id: str,
    content_type: str,
    byte_length: int,
    captured_at: datetime,
    expires_at: datetime,
) -> str:
    media_id = f"med_{uuid.uuid4().hex}"
    await conn.execute(
        text(
            "INSERT INTO verify.api_session_media (media_id, session_id, tenant_id, kind, attempt, vault_object_id, content_type, byte_length, captured_at, expires_at) "
            "VALUES (:m, :s, :t, :k, :a, :v, :c, :b, :cap, :exp)"
        ),
        {
            "m": media_id,
            "s": session_id,
            "t": uuid.UUID(tenant_id),
            "k": kind,
            "a": attempt,
            "v": vault_object_id,
            "c": content_type,
            "b": byte_length,
            "cap": captured_at,
            "exp": expires_at,
        },
    )
    return media_id


async def list_for_session(conn: AsyncConnection, session_id: str) -> list[dict[str, Any]]:
    got = await conn.execute(
        text(
            f"SELECT {_COLS} FROM verify.api_session_media WHERE session_id = :s ORDER BY attempt, kind"
        ),
        {"s": session_id},
    )
    return [dict(r._mapping) for r in got]


async def get(conn: AsyncConnection, session_id: str, media_id: str) -> dict[str, Any] | None:
    got = await conn.execute(
        text(
            f"SELECT {_COLS} FROM verify.api_session_media WHERE session_id = :s AND media_id = :m"
        ),
        {"s": session_id, "m": media_id},
    )
    r = got.first()
    return dict(r._mapping) if r else None


async def record_access(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    session_id: str,
    media_id: str,
    principal_id: str,
    reason: str,
) -> str:
    access_id = f"acc_{uuid.uuid4().hex}"
    await conn.execute(
        text(
            "INSERT INTO verify.api_media_access (access_id, media_id, session_id, tenant_id, principal_id, reason) VALUES (:a, :m, :s, :t, :p, :r)"
        ),
        {
            "a": access_id,
            "m": media_id,
            "s": session_id,
            "t": uuid.UUID(tenant_id),
            "p": uuid.UUID(principal_id),
            "r": reason,
        },
    )
    return access_id


async def accesses(conn: AsyncConnection, session_id: str) -> list[dict[str, Any]]:
    got = await conn.execute(
        text(
            "SELECT access_id, media_id, principal_id, reason, opened_at FROM verify.api_media_access WHERE session_id = :s ORDER BY opened_at DESC LIMIT 50"
        ),
        {"s": session_id},
    )
    return [dict(r._mapping) for r in got]
