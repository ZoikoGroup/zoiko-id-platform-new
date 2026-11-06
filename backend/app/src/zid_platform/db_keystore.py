"""PostgreSQL store for software keys (CR-060): the `KeyStore` that `ZID_KEY_PROVIDER=db` uses.

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


def _statements(table: str, events: str) -> dict[str, str]:
    """The statements for one known pair of table names (never user input), built once at import."""
    return {
        "state": f"SELECT state FROM {table} WHERE key_id = :key_id AND version = :version FOR UPDATE",
        "event": (
            f"INSERT INTO {events} (key_id, version, action, purpose, algorithm, actor) "
            "VALUES (:key_id, :version, :action, :purpose, :algorithm, :actor)"
        ),
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


_SQL = {schema: _statements(f"{schema}.key_version", f"{schema}.key_event") for schema in _SCHEMAS}


class PostgresKeyStore:
    def __init__(self, url: str, schema: str, actor: str = "process:unknown") -> None:
        if schema not in _SQL:
            raise ValueError(f"unknown key store schema {schema!r}")
        self._url = url
        self._sql = _SQL[schema]
        self._actor = actor  # who is changing keys: written to the audit trail with every change (`process:<role>` or `cli:<user>@<host>`)

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
            if result.rowcount != 1:
                return False
            await self._event(conn, row, "CREATED" if row.version == 1 else "ROTATED")
            return True

        return self._run(work)

    def update(self, row: StoredKey) -> None:
        async def work(conn: AsyncConnection) -> None:
            before = (
                await conn.execute(text(self._sql["state"]), self._params(row))
            ).scalar_one_or_none()
            if before is None:
                raise LookupError(f"unknown key version {row.key_id} v{row.version}")
            await conn.execute(text(self._sql["update"]), self._params(row))
            # a state change is its own event; the same state with different material is a re-wrap under a newer master key
            await self._event(conn, row, row.state if before != row.state else "REWRAPPED")

        self._run(work)

    async def _event(self, conn: AsyncConnection, row: StoredKey, action: str) -> None:
        await conn.execute(
            text(self._sql["event"]),
            {
                "key_id": row.key_id,
                "version": row.version,
                "action": action,
                "purpose": row.purpose,
                "algorithm": row.algorithm,
                "actor": self._actor,
            },
        )

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
