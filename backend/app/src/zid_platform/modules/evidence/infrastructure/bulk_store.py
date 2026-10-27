"""Bulk evidence export rows (API-13)."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection


async def insert(
    conn: AsyncConnection,
    *,
    export_id: str,
    tenant_id: str,
    requested_by_handle: str,
    purpose: str,
    case_commitment: str,
    export_mode: str,
    receipt_count: int,
    package_hash: str,
    package: dict[str, Any],
    created_at: datetime,
    expires_at: datetime,
) -> None:
    await conn.execute(
        text(
            "INSERT INTO evidence.bulk_export (export_id, tenant_id, requested_by_handle, purpose, "
            "case_commitment, export_mode, receipt_count, package_hash, package, created_at, expires_at) "
            "VALUES (:id, :t, :who, :why, :c, :m, :n, :h, CAST(:p AS jsonb), :at, :exp)"
        ),
        {
            "id": uuid.UUID(export_id),
            "t": uuid.UUID(tenant_id),
            "who": requested_by_handle,
            "why": purpose,
            "c": case_commitment,
            "m": export_mode,
            "n": receipt_count,
            "h": package_hash,
            "p": json.dumps(package),
            "at": created_at,
            "exp": expires_at,
        },
    )


async def load(conn: AsyncConnection, export_id: str) -> dict[str, Any] | None:
    row = (
        await conn.execute(
            text("SELECT * FROM evidence.bulk_export WHERE export_id = :i FOR UPDATE"),
            {"i": uuid.UUID(export_id)},
        )
    ).first()
    return dict(row._mapping) if row else None


async def count_download(conn: AsyncConnection, export_id: str) -> None:
    await conn.execute(
        text("UPDATE evidence.bulk_export SET downloads = downloads + 1 WHERE export_id = :i"),
        {"i": uuid.UUID(export_id)},
    )


async def erase(conn: AsyncConnection, export_id: str, now: datetime) -> None:
    await conn.execute(
        text(
            "UPDATE evidence.bulk_export SET package = NULL, status = 'EXPIRED', erased_at = :now "
            "WHERE export_id = :i AND status = 'AVAILABLE'"
        ),
        {"i": uuid.UUID(export_id), "now": now},
    )


async def expired_ids(conn: AsyncConnection, now: datetime, limit: int = 500) -> list[str]:
    rows = (
        await conn.execute(
            text(
                "SELECT export_id FROM evidence.bulk_export WHERE status = 'AVAILABLE' AND expires_at <= :now "
                "ORDER BY expires_at LIMIT :n"
            ),
            {"now": now, "n": limit},
        )
    ).all()
    return [str(r.export_id) for r in rows]


async def list_recent(conn: AsyncConnection, limit: int) -> list[dict[str, Any]]:
    rows = (
        await conn.execute(
            text(
                "SELECT export_id, requested_by_handle, purpose, export_mode, receipt_count, package_hash, "
                "status, downloads, created_at, expires_at, erased_at FROM evidence.bulk_export "
                "ORDER BY created_at DESC LIMIT :n"
            ),
            {"n": limit},
        )
    ).all()
    return [dict(r._mapping) for r in rows]
