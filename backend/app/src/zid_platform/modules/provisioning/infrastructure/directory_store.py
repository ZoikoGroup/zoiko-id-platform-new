"""Persistence of directory syncs (platform.directory_sync). The worker lists due syncs across companies; everything else is tenant-scoped."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.provisioning.domain.directory import DirectoryError

_COLS = (
    "sync_id, tenant_id, source_id, connector_id, provider, display_name, settings, status, interval_seconds, next_run_at, last_run_at, "
    "last_outcome, last_error, last_counts, consecutive_failures, revision, created_at, updated_at"
)


@dataclass(frozen=True, slots=True)
class Sync:
    sync_id: str
    tenant_id: str
    source_id: str
    connector_id: str
    provider: str
    display_name: str
    settings: dict[str, Any]
    status: str
    interval_seconds: int
    next_run_at: datetime
    last_run_at: datetime | None
    last_outcome: str | None
    last_error: str | None
    last_counts: dict[str, int]
    consecutive_failures: int
    revision: int
    created_at: datetime
    updated_at: datetime


def _sync(r: Any) -> Sync:
    return Sync(
        str(r.sync_id), str(r.tenant_id), str(r.source_id), str(r.connector_id), r.provider, r.display_name, dict(r.settings), r.status,
        r.interval_seconds, r.next_run_at, r.last_run_at, r.last_outcome, r.last_error, dict(r.last_counts), r.consecutive_failures, r.revision,
        r.created_at, r.updated_at,
    )  # fmt: skip


async def create(
    conn: AsyncConnection, *, tenant_id: str, source_id: str, connector_id: str, provider: str, display_name: str, settings: dict[str, Any],
    interval_seconds: int, now: datetime,
) -> Sync:  # fmt: skip
    try:
        row = (
            await conn.execute(
                text(
                    f"INSERT INTO platform.directory_sync (sync_id, tenant_id, source_id, connector_id, provider, display_name, settings, interval_seconds, "
                    f"next_run_at, created_at, updated_at) VALUES (:id, :t, :s, :c, :p, :n, CAST(:cfg AS jsonb), :i, :now, :now, :now) RETURNING {_COLS}"
                ),
                {
                    "id": uuid.uuid4(),
                    "t": uuid.UUID(tenant_id),
                    "s": uuid.UUID(source_id),
                    "c": uuid.UUID(connector_id),
                    "p": provider,
                    "n": display_name,
                    "cfg": json.dumps(settings),
                    "i": interval_seconds,
                    "now": now,
                },
            )
        ).one()
    except IntegrityError as e:
        raise DirectoryError(
            "SYNC_EXISTS", "this provisioning source already has a directory sync"
        ) from e
    return _sync(row)


async def get(conn: AsyncConnection, sync_id: str) -> Sync | None:
    row = (
        await conn.execute(
            text(f"SELECT {_COLS} FROM platform.directory_sync WHERE sync_id = :id"),
            {"id": uuid.UUID(sync_id)},
        )
    ).first()
    return None if row is None else _sync(row)


async def for_source(conn: AsyncConnection, source_id: str) -> Sync | None:
    row = (
        await conn.execute(
            text(f"SELECT {_COLS} FROM platform.directory_sync WHERE source_id = :id"),
            {"id": uuid.UUID(source_id)},
        )
    ).first()
    return None if row is None else _sync(row)


async def listing(conn: AsyncConnection) -> list[Sync]:
    rows = await conn.execute(
        text(f"SELECT {_COLS} FROM platform.directory_sync ORDER BY created_at")
    )
    return [_sync(r) for r in rows]


async def due(conn: AsyncConnection, now: datetime, limit: int = 20) -> list[Sync]:
    """Worker pass (sees every company): ACTIVE syncs whose time has come, oldest first."""
    rows = await conn.execute(
        text(
            f"SELECT {_COLS} FROM platform.directory_sync WHERE status = 'ACTIVE' AND next_run_at <= :now ORDER BY next_run_at LIMIT :n"
        ),
        {"now": now, "n": limit},
    )
    return [_sync(r) for r in rows]


async def record_run(
    conn: AsyncConnection, sync: Sync, *, ok: bool, error: str | None, counts: dict[str, int], now: datetime, delay_s: int, failures: int
) -> None:  # fmt: skip
    await conn.execute(
        text(
            "UPDATE platform.directory_sync SET last_run_at = :now, last_outcome = :o, last_error = :e, last_counts = CAST(:c AS jsonb), "
            "consecutive_failures = :f, next_run_at = :next, revision = revision + 1, updated_at = :now WHERE sync_id = :id"
        ),
        {"now": now, "o": "OK" if ok else "FAILED", "e": error, "c": json.dumps(counts), "f": failures, "next": now + timedelta(seconds=delay_s),
         "id": uuid.UUID(sync.sync_id)},
    )  # fmt: skip


async def set_status(
    conn: AsyncConnection, sync_id: str, status: str, now: datetime
) -> Sync | None:
    row = (
        await conn.execute(
            text(
                f"UPDATE platform.directory_sync SET status = :s, next_run_at = CASE WHEN :s = 'ACTIVE' THEN :now ELSE next_run_at END, "
                f"revision = revision + 1, updated_at = :now WHERE sync_id = :id RETURNING {_COLS}"
            ),
            {"s": status, "now": now, "id": uuid.UUID(sync_id)},
        )
    ).first()
    return None if row is None else _sync(row)


async def run_now(conn: AsyncConnection, sync_id: str, now: datetime) -> None:
    """Ask the worker to run this sync on its next pass."""
    await conn.execute(
        text(
            "UPDATE platform.directory_sync SET next_run_at = :now, updated_at = :now WHERE sync_id = :id AND status = 'ACTIVE'"
        ),
        {"now": now, "id": uuid.UUID(sync_id)},
    )
