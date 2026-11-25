"""Application logos in PostgreSQL (CR-166). A company reads and writes its own under row-level security; the customer's page reads an APPROVED one by the session's link inside `auth_scope`; the operator plane reads
and decides pending ones inside `go_live_store.operator_scope`. Nothing is deleted: a removed logo keeps its row, emptied."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

_META = "application_id, tenant_id, status, content_type, digest, uploaded_by, uploaded_at, decided_by, decided_at, note"


def _row(r: Any) -> dict[str, Any]:
    return dict(r._mapping)


async def put(
    conn: AsyncConnection,
    *,
    application_id: str,
    tenant_id: str,
    image: bytes,
    digest: bytes,
    status: str,
    uploaded_by: str,
    now: datetime,
) -> None:
    """The one logo of an application, replacing any earlier one (which goes back to the status it is uploaded with)."""
    await conn.execute(
        text(
            "INSERT INTO verify.api_logo (application_id, tenant_id, status, image, digest, uploaded_by, uploaded_at) "
            "VALUES (:a, :t, :s, :img, :d, :u, :now) "
            "ON CONFLICT (application_id) DO UPDATE SET status = EXCLUDED.status, image = EXCLUDED.image, digest = EXCLUDED.digest, "
            "uploaded_by = EXCLUDED.uploaded_by, uploaded_at = EXCLUDED.uploaded_at, decided_by = NULL, decided_at = NULL, note = NULL"
        ),
        {
            "a": uuid.UUID(application_id),
            "t": uuid.UUID(tenant_id),
            "s": status,
            "img": image,
            "d": digest,
            "u": uuid.UUID(uploaded_by),
            "now": now,
        },
    )


async def meta(conn: AsyncConnection, application_id: str) -> dict[str, Any] | None:
    row = (
        await conn.execute(
            text(f"SELECT {_META} FROM verify.api_logo WHERE application_id = :a"),
            {"a": uuid.UUID(application_id)},
        )
    ).first()
    return _row(row) if row else None


async def image(conn: AsyncConnection, application_id: str, *, approved_only: bool) -> bytes | None:
    row = (
        await conn.execute(
            text(
                "SELECT image FROM verify.api_logo WHERE application_id = :a AND status <> 'REMOVED'"
                + (" AND status = 'APPROVED'" if approved_only else "")
            ),
            {"a": uuid.UUID(application_id)},
        )
    ).first()
    return bytes(row.image) if row else None


async def remove(conn: AsyncConnection, application_id: str) -> None:
    await conn.execute(
        text(
            "UPDATE verify.api_logo SET status = 'REMOVED', image = ''::bytea, decided_by = NULL, decided_at = NULL, note = NULL WHERE application_id = :a"
        ),
        {"a": uuid.UUID(application_id)},
    )


async def pending(conn: AsyncConnection, *, limit: int = 20) -> list[dict[str, Any]]:
    """Operator plane: every company's logos waiting for a decision, oldest first, with the image so a person can look at it."""
    rows = (
        await conn.execute(
            text(
                "SELECT l.application_id, l.tenant_id, l.digest, l.uploaded_at, l.image, a.name AS application_name, a.environment "
                "FROM verify.api_logo l JOIN verify.api_application a USING (application_id) "
                "WHERE l.status = 'PENDING' ORDER BY l.uploaded_at LIMIT :n"
            ),
            {"n": limit},
        )
    ).all()
    return [_row(r) for r in rows]


async def decide(
    conn: AsyncConnection,
    application_id: str,
    digest: bytes,
    status: str,
    operator: str,
    note: str | None,
    now: datetime,
) -> bool:
    """Decide the pending logo whose digest this is. False when it is no longer that image or no longer pending: the operator saw something that has since changed."""
    row = (
        await conn.execute(
            text(
                "UPDATE verify.api_logo SET status = :s, decided_by = :o, decided_at = :now, note = :n "
                "WHERE application_id = :a AND status = 'PENDING' AND digest = :d RETURNING application_id"
            ),
            {
                "s": status,
                "o": uuid.UUID(operator),
                "now": now,
                "n": note or None,
                "a": uuid.UUID(application_id),
                "d": digest,
            },
        )
    ).first()
    return row is not None
