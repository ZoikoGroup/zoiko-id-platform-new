"""Production-credential approvals in PostgreSQL (CR-162). Scoped by row-level security to the company; an approval is spent with one conditional update, so two requests cannot spend it twice."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

_APPROVAL = (
    "approval_id, tenant_id, application_id, action, credential_id, key_digest, status, requested_by, decided_by, note, "
    "created_at, expires_at, decided_at, consumed_at"
)


def _row(r: Any) -> dict[str, Any]:
    return dict(r._mapping)


async def insert_approval(
    conn: AsyncConnection,
    *,
    approval_id: str,
    tenant_id: str,
    application_id: str,
    action: str,
    credential_id: str | None,
    key_digest: bytes | None,
    requested_by: str,
    expires_at: datetime,
) -> None:
    await conn.execute(
        text(
            "INSERT INTO verify.api_credential_approval (approval_id, tenant_id, application_id, action, credential_id, key_digest, requested_by, expires_at) "
            "VALUES (:i, :t, :a, :act, :c, :k, :by, :x)"
        ),
        {
            "i": uuid.UUID(approval_id),
            "t": uuid.UUID(tenant_id),
            "a": uuid.UUID(application_id),
            "act": action,
            "c": uuid.UUID(credential_id) if credential_id else None,
            "k": key_digest,
            "by": uuid.UUID(requested_by),
            "x": expires_at,
        },
    )


async def get_approval(
    conn: AsyncConnection, approval_id: str, *, lock: bool = False
) -> dict[str, Any] | None:
    row = (
        await conn.execute(
            text(
                f"SELECT {_APPROVAL} FROM verify.api_credential_approval WHERE approval_id = :i"
                + (" FOR UPDATE" if lock else "")
            ),
            {"i": uuid.UUID(approval_id)},
        )
    ).first()
    return _row(row) if row else None


async def list_approvals(conn: AsyncConnection, *, limit: int = 50) -> list[dict[str, Any]]:
    rows = (
        await conn.execute(
            text(
                f"SELECT {_APPROVAL} FROM verify.api_credential_approval ORDER BY created_at DESC LIMIT :n"
            ),
            {"n": limit},
        )
    ).all()
    return [_row(r) for r in rows]


async def decide(
    conn: AsyncConnection,
    approval_id: str,
    status: str,
    decided_by: str | None,
    note: str | None,
    now: datetime,
) -> None:
    await conn.execute(
        text(
            "UPDATE verify.api_credential_approval SET status = :s, decided_by = :d, note = :n, decided_at = :at "
            "WHERE approval_id = :i AND status = 'PENDING'"
        ),
        {
            "i": uuid.UUID(approval_id),
            "s": status,
            "d": uuid.UUID(decided_by) if decided_by else None,
            "n": note or None,
            "at": now,
        },
    )


async def consume(conn: AsyncConnection, approval_id: str, now: datetime) -> bool:
    """Spend an approved approval once. False when it was already spent or is no longer approved: the caller refuses."""
    row = (
        await conn.execute(
            text(
                "UPDATE verify.api_credential_approval SET status = 'CONSUMED', consumed_at = :n "
                "WHERE approval_id = :i AND status = 'APPROVED' AND expires_at > :n RETURNING approval_id"
            ),
            {"i": uuid.UUID(approval_id), "n": now},
        )
    ).first()
    return row is not None
