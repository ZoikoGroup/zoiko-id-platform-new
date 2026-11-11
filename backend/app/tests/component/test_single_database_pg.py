"""The cell and global planes share ONE database: the global streams add only the `global_directory` schema,
the cell streams never touch it, and migrating twice (or in either order) changes nothing."""

from __future__ import annotations

import asyncio
import uuid

from conftest import ADMIN_URL, SCHEMAS_SQL
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from zid_platform.migrations import split_sql, streams_with_revisions, upgrade


async def _run(url: object, *statements: str) -> None:
    eng = create_async_engine(url, isolation_level="AUTOCOMMIT")  # type: ignore[arg-type]
    async with eng.connect() as c:
        for s in statements:
            await c.exec_driver_sql(s)
    await eng.dispose()


async def _rows(url: object, sql: str) -> list[str]:
    eng = create_async_engine(url)  # type: ignore[arg-type]
    async with eng.connect() as c:
        rows = [r[0] for r in (await c.execute(text(sql))).all()]
    await eng.dispose()
    return rows


def test_both_planes_migrate_into_one_database_and_stay_in_their_own_schemas() -> None:
    assert ADMIN_URL
    admin = make_url(ADMIN_URL)
    db = f"zid_single_{uuid.uuid4().hex[:8]}"
    url = admin.set(database=db)
    rendered = url.render_as_string(hide_password=False)
    asyncio.run(_run(admin, f'CREATE DATABASE "{db}"'))
    try:
        # a database that holds only the cell schemas, as one created before the planes were merged
        asyncio.run(_run(url, *split_sql(SCHEMAS_SQL.read_text(encoding="utf-8"))))
        assert "global_directory" not in asyncio.run(_rows(url, "SELECT nspname FROM pg_namespace"))

        upgrade(rendered)  # cell streams
        assert not any(
            t.startswith("global_directory.")
            for t in asyncio.run(
                _rows(
                    url,
                    "SELECT table_schema || '.' || table_name FROM information_schema.tables",
                )
            )
        )  # the cell streams never touch the global schema

        upgrade(rendered, role="global")  # creates the schema itself, then the global streams
        upgrade(rendered, role="global")  # idempotent
        upgrade(rendered)  # and the cell streams again, now beside the global ones

        tables = set(
            asyncio.run(
                _rows(
                    url,
                    "SELECT table_name FROM information_schema.tables WHERE table_schema = 'global_directory'",
                )
            )
        )
        assert {
            "tenant_directory_record",
            "routing_alias",
            "key_version",
            "key_event",
        } <= tables

        versions = set(
            asyncio.run(
                _rows(
                    url,
                    "SELECT tablename FROM pg_tables WHERE schemaname = 'zid_migrations'",
                )
            )
        )
        for stream in streams_with_revisions("cell") + streams_with_revisions("global"):
            assert (
                f"alembic_version__{stream}" in versions
            )  # one version table per stream, no clash
    finally:
        asyncio.run(_run(admin, f'DROP DATABASE IF EXISTS "{db}" WITH (FORCE)'))
