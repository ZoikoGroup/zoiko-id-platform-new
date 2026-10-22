"""The session module's personal data for the privacy data map: identity sessions (authentication
history, PRIV-001 §31 "short-to-medium operational retention"). Erasure revokes them."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.privacy.ports import register_store

STORE = "authn.session"


async def _locate(conn: AsyncConnection, subject: str) -> list[dict[str, Any]]:
    rows = (
        await conn.execute(
            text("SELECT session_id, created_at FROM authn.session WHERE principal_id = :p"),
            {"p": uuid.UUID(subject)},
        )
    ).all()
    return [
        {
            "object_ref": str(r.session_id),
            "object_type": "session.identity_session",
            "purpose_code": "AUTHENTICATE_USER",
            "data_category": "D2",
            "trigger_at": r.created_at,
            "region": None,
            "retention_policy_code": "SECURITY_LOG",
        }
        for r in rows
    ]


async def _export(conn: AsyncConnection, entry: Row[Any]) -> dict[str, Any]:
    row = (
        await conn.execute(
            text(
                "SELECT auth_time, aal, status::text AS status, created_at, last_seen_at, expires_at "
                "FROM authn.session WHERE session_id = :s"
            ),
            {"s": uuid.UUID(entry.object_ref)},
        )
    ).first()
    return dict(row._mapping) if row else {}


async def _dispose(conn: AsyncConnection, entry: Row[Any], action: str) -> None:
    await conn.execute(
        text("""
            UPDATE authn.session SET status = 'REVOKED', revoked_at = coalesce(revoked_at, now())
            WHERE session_id = :s AND status IN ('ACTIVE', 'REAUTH_REQUIRED')
        """),
        {"s": uuid.UUID(entry.object_ref)},
    )


def register() -> None:
    register_store(STORE, locator=_locate, exporter=_export, disposer=_dispose)


__all__ = ["register"]
