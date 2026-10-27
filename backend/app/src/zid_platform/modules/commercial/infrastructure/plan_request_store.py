"""Plan requests in PostgreSQL (CR-032). Tenant rows are scoped by RLS; the operator reads across companies only inside
`operator_scope`, which the operator surface sets for the one transaction it needs."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

_COLS = (
    "request_id, tenant_id, plan_code, current_plan, note, requested_by, status, created_at, decided_by, decided_at, "
    "decision_note, company_name"
)


@asynccontextmanager
async def operator_scope(conn: AsyncConnection) -> AsyncIterator[None]:
    """Let the operator plane see and answer every company's requests for the enclosed statements only."""
    await conn.execute(text("SELECT set_config('app.commercial_scope', 'operator', true)"))
    try:
        yield
    finally:
        await conn.execute(text("SELECT set_config('app.commercial_scope', '', true)"))


def _row(r: Any) -> dict[str, Any]:
    return dict(r._mapping)


async def insert(
    conn: AsyncConnection,
    *,
    request_id: str,
    tenant_id: str,
    plan_code: str,
    current_plan: str,
    note: str | None,
    requested_by: str,
    company_name: str | None = None,
) -> None:
    await conn.execute(
        text(
            "INSERT INTO commercial.plan_request (request_id, tenant_id, plan_code, current_plan, note, requested_by, "
            "company_name) VALUES (:id, :t, :plan, :cur, :note, :by, :co)"
        ),
        {
            "id": uuid.UUID(request_id),
            "t": uuid.UUID(tenant_id),
            "plan": plan_code,
            "cur": current_plan,
            "note": note or None,
            "by": uuid.UUID(requested_by),
            "co": company_name,
        },
    )


async def has_open(conn: AsyncConnection) -> bool:
    return bool(
        (
            await conn.execute(
                text("SELECT 1 FROM commercial.plan_request WHERE status = 'OPEN' LIMIT 1")
            )
        ).first()
    )


async def mine(conn: AsyncConnection, limit: int = 20) -> list[dict[str, Any]]:
    rows = (
        await conn.execute(
            text(f"SELECT {_COLS} FROM commercial.plan_request ORDER BY created_at DESC LIMIT :n"),
            {"n": limit},
        )
    ).all()
    return [_row(r) for r in rows]


async def load(conn: AsyncConnection, request_id: str) -> dict[str, Any] | None:
    row = (
        await conn.execute(
            text(f"SELECT {_COLS} FROM commercial.plan_request WHERE request_id = :i FOR UPDATE"),
            {"i": uuid.UUID(request_id)},
        )
    ).first()
    return _row(row) if row else None


async def withdraw(conn: AsyncConnection, request_id: str) -> None:
    await conn.execute(
        text(
            "UPDATE commercial.plan_request SET status = 'WITHDRAWN' WHERE request_id = :i AND status = 'OPEN'"
        ),
        {"i": uuid.UUID(request_id)},
    )


async def open_for_operator(conn: AsyncConnection, limit: int = 100) -> list[dict[str, Any]]:
    rows = (
        await conn.execute(
            text(
                f"SELECT {_COLS} FROM commercial.plan_request WHERE status = 'OPEN' ORDER BY created_at LIMIT :n"
            ),
            {"n": limit},
        )
    ).all()
    return [_row(r) for r in rows]


async def answer(
    conn: AsyncConnection, request_id: str, *, status: str, by: str, note: str | None, now: datetime
) -> None:
    await conn.execute(
        text(
            "UPDATE commercial.plan_request SET status = :s, decided_by = :by, decided_at = :at, decision_note = :n "
            "WHERE request_id = :i AND status = 'OPEN'"
        ),
        {
            "s": status,
            "by": uuid.UUID(by),
            "at": now,
            "n": note or None,
            "i": uuid.UUID(request_id),
        },
    )
