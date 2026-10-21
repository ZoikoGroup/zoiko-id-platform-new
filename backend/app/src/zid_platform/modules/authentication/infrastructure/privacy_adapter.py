"""The authentication module's personal data for the privacy data map (PRIV-001 §31: "WebAuthn
public credentials/authenticator metadata: while active + bounded security tail").

Erasure revokes every authenticator (the credential can never sign in again) and removes its
user-chosen label; the public key record itself is immutable by design (it is replaced, never
rewritten) and carries no personal data beyond the account link, which the erased tombstone keeps.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.privacy.ports import register_store

STORE = "authn.authenticator"


async def _locate(conn: AsyncConnection, subject: str) -> list[dict[str, Any]]:
    rows = (
        await conn.execute(
            text("SELECT authenticator_id, authenticator_type, registered_at FROM authn.authenticator WHERE principal_id = :p"),
            {"p": uuid.UUID(subject)},
        )
    ).all()
    return [
        {
            "object_ref": str(r.authenticator_id),
            "object_type": f"authenticator.{r.authenticator_type}".lower(),
            "purpose_code": "AUTHENTICATE_USER",
            "data_category": "D2",
            "trigger_at": r.registered_at,
            "region": None,
            "retention_policy_code": "AUTHENTICATOR_LIFETIME",
        }
        for r in rows
    ]


async def _export(conn: AsyncConnection, entry: Row[Any]) -> dict[str, Any]:
    row = (
        await conn.execute(
            text(
                "SELECT authenticator_type, label, status::text AS status, backup_eligible, registered_at, "
                "last_used_at, revoked_at FROM authn.authenticator WHERE authenticator_id = :a"
            ),
            {"a": uuid.UUID(entry.object_ref)},
        )
    ).first()
    return dict(row._mapping) if row else {}


async def _dispose(conn: AsyncConnection, entry: Row[Any], action: str) -> None:
    await conn.execute(
        text("""
            UPDATE authn.authenticator SET label = NULL,
                status = CASE WHEN status IN ('ACTIVE', 'SUSPENDED', 'PENDING_REGISTRATION')
                              THEN 'REVOKED'::authn.authenticator_status ELSE status END,
                revoked_at = coalesce(revoked_at, now()), revoked_reason = coalesce(revoked_reason, 'ERASED')
            WHERE authenticator_id = :a
        """),
        {"a": uuid.UUID(entry.object_ref)},
    )


def register() -> None:
    register_store(STORE, locator=_locate, exporter=_export, disposer=_dispose)


__all__ = ["register"]
