"""Usage counters in PostgreSQL (`metering.usage_event`, RLS on, owner: metering)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.metering.domain.usage import UsageKey


async def add(conn: AsyncConnection, key: UsageKey, calls: int) -> None:
    await conn.execute(
        text(
            "INSERT INTO metering.usage_event (tenant_id, bucket, surface, client_id, status_class, calls) "
            "VALUES (:t, :b, :s, :c, :k, :n) ON CONFLICT (tenant_id, bucket, surface, client_id, status_class) "
            "DO UPDATE SET calls = metering.usage_event.calls + EXCLUDED.calls"
        ),
        {
            "t": uuid.UUID(key.tenant_id),
            "b": key.bucket,
            "s": key.surface,
            "c": key.client_id[:128],
            "k": key.status_class,
            "n": calls,
        },
    )


async def summary(conn: AsyncConnection, *, days: int) -> list[dict[str, Any]]:
    """Daily calls per surface and status class for the last `days` days (UTC), oldest first."""
    rows = (
        await conn.execute(
            text(
                "SELECT date_trunc('day', bucket) AS day, surface, status_class, sum(calls)::bigint AS calls "
                "FROM metering.usage_event WHERE bucket >= date_trunc('day', now()) - make_interval(days => :d) "
                "GROUP BY 1, 2, 3 ORDER BY 1, 2, 3"
            ),
            {"d": days - 1},
        )
    ).all()
    return [
        {
            "day": r.day.date().isoformat(),
            "surface": r.surface,
            "status_class": r.status_class,
            "calls": int(r.calls),
        }
        for r in rows
    ]


async def by_client(conn: AsyncConnection, *, days: int) -> list[dict[str, Any]]:
    rows = (
        await conn.execute(
            text(
                "SELECT client_id, sum(calls)::bigint AS calls, "
                "sum(calls) FILTER (WHERE status_class IN ('4xx', '5xx'))::bigint AS errors "
                "FROM metering.usage_event WHERE bucket >= date_trunc('day', now()) - make_interval(days => :d) "
                "GROUP BY client_id ORDER BY 2 DESC LIMIT 50"
            ),
            {"d": days - 1},
        )
    ).all()
    return [
        {"client_id": r.client_id, "calls": int(r.calls), "errors": int(r.errors or 0)}
        for r in rows
    ]
