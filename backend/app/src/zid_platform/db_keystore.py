"""PostgreSQL store for software keys (CR-057): the `KeyStore` that `ZID_KEY_PROVIDER=db` uses.

Keys are needed while the application is being built, which can happen with an event loop already running (uvicorn imports the
app inside its loop), so every call runs its query on a private event loop in a short-lived thread. The store is used at
start-up, on rotation and on destruction; it is not on the request path.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any, TypeVar

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine
from sqlalchemy.pool import NullPool
from zid_crypto.software_keys import StoredKey

T = TypeVar("T")
_COLUMNS = (
    "key_id, version, purpose, algorithm, state, wrapped, public_jwk, pq_ready, "
    "created_at, activated_at, retired_at, destroyed_at"
)
_SCHEMAS = ("keys", "global_directory")


def _statements(table: str) -> dict[str, str]:
    """The three statements for one known table name (never user input), built once at import."""
    return {
        "select": f"SELECT {_COLUMNS} FROM {table} ORDER BY key_id, version",
        "insert": (
            f"INSERT INTO {table} ({_COLUMNS}) VALUES (:key_id, :version, :purpose, :algorithm, :state, :wrapped, "
            "CAST(:public_jwk AS jsonb), :pq_ready, :created_at, :activated_at, :retired_at, :destroyed_at) "
            "ON CONFLICT (key_id, version) DO NOTHING"
        ),
        "update": (
            f"UPDATE {table} SET state = :state, wrapped = :wrapped, activated_at = :activated_at, "
            "retired_at = :retired_at, destroyed_at = :destroyed_at WHERE key_id = :key_id AND version = :version"
        ),
    }


_SQL = {schema: _statements(f"{schema}.key_version") for schema in _SCHEMAS}


class PostgresKeyStore:
    def __init__(self, url: str, schema: str) -> None:
        if schema not in _SQL:
            raise ValueError(f"unknown key store schema {schema!r}")
        self._url = url
        self._sql = _SQL[schema]

    def _run(self, work: Callable[[AsyncConnection], Awaitable[T]]) -> T:
        async def go() -> T:
            engine = create_async_engine(self._url, poolclass=NullPool)
            try:
                async with engine.begin() as conn:
                    return await work(conn)
            finally:
                await engine.dispose()

        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, go()).result()

    def load_all(self) -> list[StoredKey]:
        async def work(conn: AsyncConnection) -> list[StoredKey]:
            rows = (await conn.execute(text(self._sql["select"]))).all()
            return [
                StoredKey(
                    key_id=r.key_id,
                    version=r.version,
                    purpose=r.purpose,
                    algorithm=r.algorithm,
                    state=r.state,
                    wrapped=bytes(r.wrapped) if r.wrapped is not None else None,
                    public_jwk=r.public_jwk,
                    created_at=r.created_at,
                    activated_at=r.activated_at,
                    retired_at=r.retired_at,
                    destroyed_at=r.destroyed_at,
                    pq_ready=r.pq_ready,
                )
                for r in rows
            ]

        return self._run(work)

    def insert(self, row: StoredKey) -> bool:
        async def work(conn: AsyncConnection) -> bool:
            result = await conn.execute(text(self._sql["insert"]), self._params(row))
            return result.rowcount == 1

        return self._run(work)

    def update(self, row: StoredKey) -> None:
        async def work(conn: AsyncConnection) -> None:
            result = await conn.execute(text(self._sql["update"]), self._params(row))
            if result.rowcount != 1:
                raise LookupError(f"unknown key version {row.key_id} v{row.version}")

        self._run(work)

    @staticmethod
    def _params(row: StoredKey) -> dict[str, Any]:
        return {
            "key_id": row.key_id,
            "version": row.version,
            "purpose": row.purpose,
            "algorithm": row.algorithm,
            "state": row.state,
            "wrapped": row.wrapped,
            "public_jwk": json.dumps(row.public_jwk) if row.public_jwk is not None else None,
            "pq_ready": row.pq_ready,
            "created_at": row.created_at,
            "activated_at": row.activated_at,
            "retired_at": row.retired_at,
            "destroyed_at": row.destroyed_at,
        }
