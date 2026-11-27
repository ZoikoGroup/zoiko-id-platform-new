"""Retention of the partner request log (ZID-CP-WF10 v2.0, WF-10.10; CR-173): the log stays append-only, and exactly one function can remove rows older than the period."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest
from conftest import engine
from sqlalchemy import text


async def put(owner_url: str, tenant: uuid.UUID, app_id: uuid.UUID, *, days_old: int) -> uuid.UUID:
    log_id = uuid.uuid4()
    async with engine(owner_url) as eng, eng.begin() as c:
        await c.execute(
            text(
                "INSERT INTO verify.api_application (application_id, tenant_id, name, environment, status, scopes, redirect_origins, created_by) VALUES (:a, :t, :n, 'SANDBOX', 'ACTIVE', ARRAY['products:read'], ARRAY[]::text[], :u) ON CONFLICT DO NOTHING"
            ),
            {"a": app_id, "t": tenant, "n": "ret-" + str(app_id)[:8], "u": uuid.uuid4()},
        )
        await c.execute(
            text(
                "INSERT INTO verify.api_request_log (log_id, tenant_id, application_id, environment, method, route, status, trace_id, duration_ms, occurred_at) VALUES (:i, :t, :a, 'SANDBOX', 'GET', '/v1/verify-api/whoami', 200, 'cor_x', 5, now() - make_interval(days => :d))"
            ),
            {"i": log_id, "t": tenant, "a": app_id, "d": days_old},
        )
    return log_id


async def count(owner_url: str, ids: list[uuid.UUID]) -> int:
    async with engine(owner_url) as eng, eng.begin() as c:
        return int(
            (
                await c.execute(
                    text("SELECT count(*) FROM verify.api_request_log WHERE log_id = ANY(:i)"),
                    {"i": ids},
                )
            ).scalar_one()
        )


def test_the_log_loses_only_rows_older_than_the_period_and_only_through_the_purge_function(
    owner_url: str,
) -> None:
    tenant, app_id = uuid.uuid4(), uuid.uuid4()

    async def go() -> None:
        old = await put(owner_url, tenant, app_id, days_old=120)
        edge = await put(owner_url, tenant, app_id, days_old=60)
        new = await put(owner_url, tenant, app_id, days_old=0)
        mine = [old, edge, new]

        async def direct(sql: str) -> None:
            async with engine(owner_url) as eng, eng.begin() as c:
                await c.execute(text(sql), {"i": old})

        for sql in (
            "DELETE FROM verify.api_request_log WHERE log_id = :i",
            "UPDATE verify.api_request_log SET status = 500 WHERE log_id = :i",
        ):
            with pytest.raises(Exception, match="append-only"):
                await direct(sql)  # an old row is still protected from a direct change
        assert await count(owner_url, mine) == 3

        async def purge(days: int) -> Any:
            async with engine(owner_url) as eng, eng.begin() as c:
                return (
                    await c.execute(text("SELECT verify.api_purge_request_log(:d)"), {"d": days})
                ).scalar_one()

        with pytest.raises(Exception, match="at least 90 days"):
            await purge(30)  # the period cannot be set short to wipe recent rows
        assert await count(owner_url, mine) == 3
        removed = await purge(90)
        assert (
            removed >= 1
            and await count(owner_url, [old]) == 0
            and await count(owner_url, [edge, new]) == 2
        )  # 120 days old went; 60 and 0 stayed
        assert await purge(90) >= 0 and await count(owner_url, [edge, new]) == 2
        # the permission is gone again afterwards: the setting does not leak
        with pytest.raises(Exception, match="append-only"):
            async with engine(owner_url) as eng, eng.begin() as c:
                await c.execute(
                    text("DELETE FROM verify.api_request_log WHERE log_id = :i"), {"i": edge}
                )

    asyncio.run(go())


def test_the_scheduled_maintenance_job_runs_the_purge_and_reports_how_many_rows_it_removed(
    owner_url: str,
) -> None:
    from datetime import UTC, datetime

    from zid_platform.entrypoints import maintenance

    tenant, app_id = uuid.uuid4(), uuid.uuid4()

    async def go() -> None:
        old = await put(owner_url, tenant, app_id, days_old=200)
        new = await put(owner_url, tenant, app_id, days_old=1)
        async with engine(owner_url) as eng, eng.begin() as c:
            counts = await maintenance.run_once(c, datetime.now(UTC))
        assert counts["purged:verify.api_request_log"] >= 1
        assert (
            await count(owner_url, [old]) == 0 and await count(owner_url, [new]) == 1
        )  # the old row went on the schedule, the recent one stayed

    asyncio.run(go())
