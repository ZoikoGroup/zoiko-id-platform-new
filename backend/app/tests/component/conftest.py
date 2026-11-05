"""Shared PostgreSQL fixture for component tests.

Needs a disposable PostgreSQL superuser URL in ZID_TEST_PG_URL (postgresql+asyncpg://...); every
test here is skipped otherwise. One database per session: schemas bootstrapped, every migration
stream applied, and a runtime role WITHOUT BYPASSRLS so row-level security is really in force.

    docker run -d --rm --name zid-ledger-test -e POSTGRES_PASSWORD=test -p 55432:5432 postgres:16-alpine
    ZID_TEST_PG_URL=postgresql+asyncpg://postgres:test@localhost:55432/postgres uv run pytest app/tests/component
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from zid_context import RequestContext
from zid_platform.migrations import split_sql, upgrade

ADMIN_URL = os.environ.get("ZID_TEST_PG_URL")
SCHEMAS_SQL = Path(__file__).resolve().parents[3] / "database" / "schemas" / "001_cell_schemas.sql"
RT_ROLE, RT_PASSWORD = "zid_component_rt", "rt-test-only"
RUNTIME_SCHEMAS = (
    "agent",
    "core",
    "org",
    "authz",
    "events",
    "platform",
    "authn",
    "signals",
    "verify",
    "cred",
    "evidence",
    "privacy",
    "metering",
    "commercial",
)
CELL = "00000000-0000-7000-8000-0000000000c1"


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    if not ADMIN_URL:
        skip = pytest.mark.skip(reason="set ZID_TEST_PG_URL to a disposable PostgreSQL")
        for item in items:
            if "component" in str(item.fspath):
                item.add_marker(skip)


@pytest.fixture(scope="session")
def pg_urls() -> Iterator[tuple[str, str]]:
    """(owner URL, runtime-role URL) for a fresh, fully migrated database."""
    assert ADMIN_URL
    db = f"zid_component_{uuid.uuid4().hex[:8]}"
    admin = make_url(ADMIN_URL)
    db_url = admin.set(database=db)

    async def run(url: object, *statements: str) -> None:
        eng = create_async_engine(url, isolation_level="AUTOCOMMIT")  # type: ignore[arg-type]
        async with eng.connect() as c:
            for s in statements:
                await c.exec_driver_sql(s)
        await eng.dispose()

    async def setup() -> None:
        eng = create_async_engine(admin, isolation_level="AUTOCOMMIT")
        async with eng.connect() as c:
            await c.execute(text(f'CREATE DATABASE "{db}"'))
            if not (
                await c.execute(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": RT_ROLE})
            ).first():
                await c.execute(
                    text(f"CREATE ROLE {RT_ROLE} LOGIN PASSWORD '{RT_PASSWORD}' NOBYPASSRLS")
                )
        await eng.dispose()
        await run(db_url, *split_sql(SCHEMAS_SQL.read_text(encoding="utf-8")))

    asyncio.run(setup())
    upgrade(db_url.render_as_string(hide_password=False))
    grants = [f"GRANT USAGE ON SCHEMA {s} TO {RT_ROLE}" for s in RUNTIME_SCHEMAS] + [
        f"GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA {s} TO {RT_ROLE}"
        for s in RUNTIME_SCHEMAS
    ]
    asyncio.run(run(db_url, *grants))
    owner = db_url.render_as_string(hide_password=False)
    runtime = db_url.set(username=RT_ROLE, password=RT_PASSWORD).render_as_string(
        hide_password=False
    )
    yield owner, runtime
    asyncio.run(run(admin, f'DROP DATABASE "{db}" WITH (FORCE)'))


@pytest.fixture(scope="session")
def rt_url(pg_urls: tuple[str, str]) -> str:
    return pg_urls[1]


@pytest.fixture(scope="session")
def owner_url(pg_urls: tuple[str, str]) -> str:
    return pg_urls[0]


@pytest.fixture(autouse=True)
def _own_sanction_lists(request: pytest.FixtureRequest) -> None:
    """The public lists are one set for the whole database, and a list that shrinks is refused. A test that loads its own
    lists must not meet the ones another test left, so the modules that load their own start each test with none."""
    if not any(
        part in request.module.__name__
        for part in ("screening", "org_claims_api", "org_claims_lifecycle")
    ):
        return
    owner = request.getfixturevalue("owner_url")

    async def clear() -> None:
        async with engine(owner) as eng, eng.begin() as c:
            await c.execute(text("DELETE FROM verify.sanction_list_version"))

    asyncio.run(clear())


def ctx(tenant: str | uuid.UUID) -> RequestContext:
    return RequestContext("req_t", "cor_t", "test", CELL, tenant_id=str(tenant), principal_id=None)


@asynccontextmanager
async def engine(url: str, size: int = 5) -> AsyncIterator[AsyncEngine]:
    eng = create_async_engine(url, pool_size=size, max_overflow=0, pool_timeout=600)
    try:
        yield eng
    finally:
        await eng.dispose()
