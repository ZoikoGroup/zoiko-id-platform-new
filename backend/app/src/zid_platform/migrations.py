"""Per-module Alembic streams (DEC-D1, DEC-D6; ZID-DATA-001 §1.1).

Each owning module has its own stream in `backend/app/migrations/<module>/`, with its own version
table `zid_migrations.alembic_version__<module>`, so a module's schema history can move with the
module if it is ever extracted. Schemas themselves are created once by `backend/database/schemas/`.

    python -m zid_platform.migrations upgrade --url postgresql+asyncpg://... [--stream mandate ...]

Run as the migration owner role (`db_owner_migration`), never as a runtime role.
"""

from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# Source tree: backend/app/migrations. The image installs the package non-editable and ships the
# streams at /app/migrations, pointed to by ZID_MIGRATIONS_DIR.
MIGRATIONS_DIR = Path(
    os.environ.get("ZID_MIGRATIONS_DIR") or Path(__file__).resolve().parents[2] / "migrations"
)
VERSION_SCHEMA = "zid_migrations"


def streams_with_revisions() -> list[str]:
    """Streams that have at least one revision, in a stable order."""
    return sorted(
        p.name
        for p in MIGRATIONS_DIR.iterdir()
        if p.is_dir() and any((p / "versions").glob("*.py"))
    )


def config_for(stream: str, url: str) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR / stream))
    cfg.set_main_option("sqlalchemy.url", url)
    cfg.attributes["stream"] = stream
    return cfg


def run_env(context: object) -> None:
    """Shared body of every stream's env.py (online mode only; DDL is never generated offline)."""
    from alembic import context as ctx  # the live proxy configured by Alembic for this run

    cfg = ctx.config
    stream = cfg.attributes["stream"]

    def do_run(conn: Connection) -> None:
        ctx.configure(
            connection=conn,
            version_table=f"alembic_version__{stream}",
            version_table_schema=VERSION_SCHEMA,
            transaction_per_migration=True,
        )
        with ctx.begin_transaction():
            ctx.run_migrations()

    async def main() -> None:
        engine = async_engine_from_config(
            cfg.get_section(cfg.config_ini_section, {}),
            prefix="sqlalchemy.",
            poolclass=pool.NullPool,
        )
        async with engine.connect() as conn:
            await conn.run_sync(do_run)
        await engine.dispose()

    if ctx.is_offline_mode():
        raise RuntimeError("offline SQL generation is not supported; run against a database")
    asyncio.run(main())


def upgrade(url: str, streams: list[str] | None = None, revision: str = "head") -> None:
    for stream in streams or streams_with_revisions():
        command.upgrade(config_for(stream, url), revision)


def main() -> None:
    parser = argparse.ArgumentParser(prog="zid_platform.migrations")
    parser.add_argument("action", choices=["upgrade"])
    parser.add_argument("--url", required=True)
    parser.add_argument("--stream", action="append", dest="streams")
    args = parser.parse_args()
    upgrade(args.url, args.streams)


if __name__ == "__main__":
    main()
