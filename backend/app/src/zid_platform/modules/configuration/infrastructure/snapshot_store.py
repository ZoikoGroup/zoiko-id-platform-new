"""Append-only tenant settings history (`platform.configuration_snapshot`, RLS on, owner: configuration)."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.configuration.domain.settings import DEFAULTS, SettingsError


def _iso(v: Any) -> str | None:
    return v.isoformat() if v is not None else None


def _row(r: Any) -> dict[str, Any]:
    return {
        "area": r.area,
        "revision": r.revision,
        "value": r.value,
        "commitment": r.commitment,
        "created_by": str(r.created_by),
        "created_at": _iso(r.created_at),
    }


async def current(conn: AsyncConnection, area: str) -> dict[str, Any]:
    """The area's latest revision, or its defaults at revision 0 when it was never set."""
    row = (
        await conn.execute(
            text(
                "SELECT area, revision, value, commitment, created_by, created_at "
                "FROM platform.configuration_snapshot WHERE area = :a ORDER BY revision DESC LIMIT 1"
            ),
            {"a": area},
        )
    ).first()
    if row is None:
        return {
            "area": area,
            "revision": 0,
            "value": dict(DEFAULTS[area]),
            "commitment": None,
            "created_by": None,
            "created_at": None,
        }
    return _row(row)


async def write(
    conn: AsyncConnection,
    tenant_id: str,
    area: str,
    *,
    expected_revision: int,
    value: dict[str, Any],
    commitment: str,
    actor: str,
    now: datetime,
) -> dict[str, Any]:
    """Revision expected+1, or a visible conflict when someone else got there first (UX-06)."""
    latest = await current(conn, area)
    if latest["revision"] != expected_revision:
        raise SettingsError("CONFIGURATION_CONFLICT", f"current revision is {latest['revision']}")
    try:
        # a savepoint, so a lost race on the primary key does not poison the caller's transaction
        async with conn.begin_nested():
            await conn.execute(
                text(
                    "INSERT INTO platform.configuration_snapshot (tenant_id, area, revision, value, commitment, "
                    "created_by, created_at) VALUES (:t, :a, :r, CAST(:v AS jsonb), :c, :by, :now)"
                ),
                {
                    "t": uuid.UUID(tenant_id),
                    "a": area,
                    "r": expected_revision + 1,
                    "v": json.dumps(value, sort_keys=True),
                    "c": commitment,
                    "by": uuid.UUID(actor),
                    "now": now,
                },
            )
    except IntegrityError as e:
        raise SettingsError(
            "CONFIGURATION_CONFLICT", "the setting was changed by someone else"
        ) from e
    return await current(conn, area)


async def history(conn: AsyncConnection, area: str, limit: int = 50) -> list[dict[str, Any]]:
    rows = (
        await conn.execute(
            text(
                "SELECT area, revision, value, commitment, created_by, created_at "
                "FROM platform.configuration_snapshot WHERE area = :a ORDER BY revision DESC LIMIT :n"
            ),
            {"a": area, "n": limit},
        )
    ).all()
    return [_row(r) for r in rows]
