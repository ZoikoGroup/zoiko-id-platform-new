"""Apply the assistant's SQL migrations as the OWNER role:  python -m zid_assistant.migrate

Creates the `public_assistant` schema and tables, and (local/test only) the least-privilege runtime role `zid_assistant`, which can
touch ONLY this schema: it has no access to any platform schema (ADR-02: a compromised assistant holds no tenant database access).
Deployed environments provision the role through platform tooling; this runner never creates roles there.
"""

from __future__ import annotations

import asyncio
import sys

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from zid_assistant.settings import AssistantSettings

ROLE = "zid_assistant"


def _grants(password: str | None) -> str:
    create = ""
    if password is not None:
        pw = password.replace("'", "''")
        create = f"""
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{ROLE}') THEN
    CREATE ROLE {ROLE} LOGIN PASSWORD '{pw}' NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
  END IF;
END $$;"""
    return f"""{create}
GRANT USAGE ON SCHEMA public_assistant TO {ROLE};
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public_assistant TO {ROLE};
ALTER DEFAULT PRIVILEGES IN SCHEMA public_assistant GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {ROLE};
"""


async def ensure_database(url: str) -> None:
    """Local/test only: create the assistant's OWN database on the server if it does not exist, so it shares no database with the
    platform (defence in depth on top of the schema and the role). Deployed environments provision the database through tooling."""
    target = make_url(url)
    admin = create_async_engine(target.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        async with admin.connect() as conn:
            exists = (
                await conn.execute(
                    text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": target.database}
                )
            ).first()
            if exists is None:
                await conn.execute(text(f'CREATE DATABASE "{target.database}"'))
    finally:
        await admin.dispose()


async def migrate(settings: AssistantSettings) -> list[int]:
    url = settings.migration_database_url
    if not url:
        raise SystemExit("set ZID_ASSISTANT_MIGRATION_DATABASE_URL (the owner role)")
    if not settings.is_production_like:
        await ensure_database(url)
    engine = create_async_engine(url)
    applied: list[int] = []
    try:
        async with (
            engine.begin() as conn
        ):  # one transaction: all migrations and grants commit together or not at all
            raw = await conn.get_raw_connection()
            # asyncpg: the simple query protocol runs a multi-statement script
            driver = raw.driver_connection
            assert driver is not None
            await driver.execute(
                "CREATE SCHEMA IF NOT EXISTS public_assistant; CREATE TABLE IF NOT EXISTS public_assistant.schema_version (version integer PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now());"
            )
            done = {
                r[0]
                for r in (
                    await conn.execute(text("SELECT version FROM public_assistant.schema_version"))
                ).all()
            }
            for path in sorted(settings.sql_dir.glob("[0-9][0-9][0-9]_*.sql")):
                version = int(path.name[:3])
                if version in done:
                    continue
                await driver.execute(path.read_text(encoding="utf-8"))
                await driver.execute(
                    f"INSERT INTO public_assistant.schema_version (version) VALUES ({version})"
                )
                applied.append(version)
            await driver.execute(
                _grants(None if settings.is_production_like else settings.runtime_password)
            )
    finally:
        await engine.dispose()
    return applied


def main() -> int:
    applied = asyncio.run(migrate(AssistantSettings()))
    print(f"assistant migrations applied: {applied or 'none (already current)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
