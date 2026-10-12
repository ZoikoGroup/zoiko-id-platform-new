"""API idempotency (ZID-API-001 §1.1, §4).

    stored = await begin(conn, scope=..., key=..., payload=...)
    if stored is not None:          # a completed earlier attempt: replay its response
        return stored
    ... perform the mutation in the same transaction ...
    await complete(conn, scope=..., key=..., status=201, body=...)

The record is inserted (IN_PROGRESS) in the caller's transaction before the mutation runs, so two
concurrent requests with one key serialize on the primary key; the loser waits, then replays. The
same key with a different canonical payload raises IDEMPOTENCY_KEY_REUSED (HTTP 409). Keys and
payloads are stored only as hashes.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection
from zid_errors import ZidError

__all__ = ["Scope", "StoredResponse", "begin", "canonical_hash", "complete"]

DEFAULT_TTL = timedelta(hours=24)


@dataclass(frozen=True, slots=True)
class Scope:
    tenant_id: str
    client_id: str
    endpoint: str  # method + route template, e.g. "POST /v1/mandates/{id}/reservations"


@dataclass(frozen=True, slots=True)
class StoredResponse:
    status: int
    body: Any


def canonical_hash(payload: Any) -> bytes:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).digest()


def _key(key: str) -> bytes:
    if not 1 <= len(key) <= 128:
        raise ZidError(
            "VALIDATION_FAILED", "Idempotency-Key must be 1-128 characters.", http_status=422
        )
    return hashlib.sha256(key.encode()).digest()


async def begin(
    conn: AsyncConnection, *, scope: Scope, key: str, payload: Any, ttl: timedelta = DEFAULT_TTL
) -> StoredResponse | None:
    """Claim the key, or return the stored response of a completed earlier attempt."""
    kh, rh = _key(key), canonical_hash(payload)
    params = {"t": uuid.UUID(scope.tenant_id), "c": scope.client_id, "e": scope.endpoint, "k": kh}
    inserted = await conn.execute(
        text("""
            INSERT INTO platform.idempotency_record (tenant_id, client_id, endpoint, key_hash, request_hash, status,
                expires_at)
            VALUES (:t, :c, :e, :k, :r, 'IN_PROGRESS', :exp) ON CONFLICT DO NOTHING
        """),
        {**params, "r": rh, "exp": datetime.now(UTC) + ttl},
    )
    if inserted.rowcount == 1:
        return None
    row = (
        await conn.execute(
            text(
                "SELECT request_hash, status, response_status, response_body, expires_at "
                "FROM platform.idempotency_record WHERE tenant_id = :t AND client_id = :c AND endpoint = :e "
                "AND key_hash = :k FOR UPDATE"
            ),
            params,
        )
    ).one()
    if bytes(row.request_hash) != rh:
        raise ZidError(
            "IDEMPOTENCY_KEY_REUSED",
            "This Idempotency-Key was used with a different request.",
            http_status=409,
        )
    if row.status != "COMPLETED":
        raise ZidError(
            "IDEMPOTENCY_IN_PROGRESS",
            "The original request is still being processed.",
            http_status=409,
            retryable=True,
            retry_after_ms=500,
        )
    return StoredResponse(row.response_status, row.response_body)


async def complete(
    conn: AsyncConnection, *, scope: Scope, key: str, status: int, body: Any
) -> None:
    await conn.execute(
        text(
            "UPDATE platform.idempotency_record SET status = 'COMPLETED', response_status = :s, "
            "response_body = CAST(:b AS jsonb) WHERE tenant_id = :t AND client_id = :c AND endpoint = :e "
            "AND key_hash = :k"
        ),
        {
            "s": status,
            "b": json.dumps(body, default=str),
            "t": uuid.UUID(scope.tenant_id),
            "c": scope.client_id,
            "e": scope.endpoint,
            "k": _key(key),
        },
    )
