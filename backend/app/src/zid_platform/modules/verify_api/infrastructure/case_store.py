"""Disputes, appeals and erasure requests in PostgreSQL (CR-159). Company reads are scoped by row-level security; the operator plane reads every company's open cases inside `go_live_store.operator_scope`."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

_CASE = (
    "case_id, tenant_id, kind, status, session_id, reason, raised_by, reviewer_id, decided_by, note, sessions_erased, "
    "created_at, reviewed_at, decided_at, updated_at"
)


def _row(r: Any) -> dict[str, Any]:
    return dict(r._mapping)


async def insert_case(
    conn: AsyncConnection,
    *,
    case_id: str,
    tenant_id: str,
    kind: str,
    session_id: str | None,
    reference_hash: bytes | None,
    reason: str,
    raised_by: str,
) -> None:
    await conn.execute(
        text(
            "INSERT INTO verify.api_case (case_id, tenant_id, kind, session_id, reference_hash, reason, raised_by) "
            "VALUES (:i, :t, :k, :s, :h, :r, :by)"
        ),
        {
            "i": uuid.UUID(case_id),
            "t": uuid.UUID(tenant_id),
            "k": kind,
            "s": session_id,
            "h": reference_hash,
            "r": reason,
            "by": uuid.UUID(raised_by),
        },
    )


async def get_case(
    conn: AsyncConnection, case_id: str, *, lock: bool = False
) -> dict[str, Any] | None:
    row = (
        await conn.execute(
            text(
                f"SELECT {_CASE}, reference_hash FROM verify.api_case WHERE case_id = :i"
                + (" FOR UPDATE" if lock else "")
            ),
            {"i": uuid.UUID(case_id)},
        )
    ).first()
    return _row(row) if row else None


async def list_cases(conn: AsyncConnection, *, limit: int = 100) -> list[dict[str, Any]]:
    """Newest first. Under a company's scope these are its own cases; inside the operator scope, every company's."""
    rows = (
        await conn.execute(
            text(f"SELECT {_CASE} FROM verify.api_case ORDER BY created_at DESC LIMIT :n"),
            {"n": limit},
        )
    ).all()
    return [_row(r) for r in rows]


async def open_cases(conn: AsyncConnection) -> list[dict[str, Any]]:
    """Operator plane: every company's open cases, oldest first."""
    rows = (
        await conn.execute(
            text(
                f"SELECT {_CASE} FROM verify.api_case WHERE status IN ('OPEN', 'IN_REVIEW') ORDER BY created_at"
            )
        )
    ).all()
    return [_row(r) for r in rows]


async def move_case(
    conn: AsyncConnection,
    case_id: str,
    status: str,
    now: datetime,
    *,
    reviewer: str | None = None,
    decider: str | None = None,
    note: str | None = None,
    sessions_erased: int | None = None,
) -> None:
    await conn.execute(
        text(
            "UPDATE verify.api_case SET status = :s, updated_at = :n, "
            "reviewer_id = CASE WHEN CAST(:r AS uuid) IS NOT NULL THEN CAST(:r AS uuid) ELSE reviewer_id END, "
            "reviewed_at = CASE WHEN CAST(:r AS uuid) IS NOT NULL THEN :n ELSE reviewed_at END, "
            "decided_by = CASE WHEN CAST(:d AS uuid) IS NOT NULL THEN CAST(:d AS uuid) ELSE decided_by END, "
            "decided_at = CASE WHEN CAST(:d AS uuid) IS NOT NULL THEN :n ELSE decided_at END, "
            "note = COALESCE(:note, note), sessions_erased = COALESCE(:e, sessions_erased) WHERE case_id = :i"
        ),
        {
            "i": uuid.UUID(case_id),
            "s": status,
            "n": now,
            "r": reviewer,
            "d": decider,
            "note": note,
            "e": sessions_erased,
        },
    )


# ------------------------------------------------------------------------------- the customer reference


_MATCH = "sha256(convert_to('verify-api-case:' || :t || ':' || customer_reference, 'UTF8')) = :h"


async def count_sessions_for(conn: AsyncConnection, tenant_id: str, reference_hash: bytes) -> int:
    """Sessions of this company sent with the customer reference whose hash this is. Inside the company's scope."""
    return int(
        (
            await conn.execute(
                text(f"SELECT count(*) FROM verify.api_session WHERE {_MATCH}"),
                {"t": tenant_id, "h": reference_hash},
            )
        ).scalar_one()
    )


async def erase_reference(conn: AsyncConnection, tenant_id: str, reference_hash: bytes) -> int:
    """Remove the customer reference, the return address and the free-form metadata from every session of this company that carries the reference. The session, its status and its decisions stay: they hold no personal data,
    so the audit trail and the usage record remain whole. Returns how many sessions were changed. Inside the company's scope."""
    result = await conn.execute(
        text(
            "UPDATE verify.api_session SET customer_reference = 'erased-' || substr(md5(session_id), 1, 12), metadata = '{}', return_origin = NULL "
            f"WHERE {_MATCH}"
        ),
        {"t": tenant_id, "h": reference_hash},
    )
    return int(result.rowcount or 0)
