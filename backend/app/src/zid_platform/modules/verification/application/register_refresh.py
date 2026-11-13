"""Refresh a provider's certification from its official register (ZID-RCG-003 RCG-G02; CR-087).

Asks the source's connector about one exact scope, preserves the register's response, and records the attestation snapshot it proves, in the
caller's transaction. A source that may not be used here is refused with its reasons and nothing is asked. A scope the register does not list is
recorded as nothing: with no fresh snapshot the attestation rules put it on HOLD.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.verification.domain import register_sources as rs
from zid_platform.modules.verification.domain.attestation import Decision, Scope, Snapshot
from zid_platform.modules.verification.infrastructure import attestation_store
from zid_platform.modules.verification.infrastructure import register_sources as registry


@dataclass(frozen=True)
class Refresh:
    listed: bool
    snapshot: Snapshot | None
    decision: Decision


async def _preserve(conn: AsyncConnection, source_id: str, reading: rs.Reading) -> str:
    digest = rs.proof_hash(reading.body)
    await conn.execute(
        text(
            "INSERT INTO verify.register_response (proof_hash, source_id, register_url, body, fetched_at) "
            "VALUES (:h, :s, :u, :b, :f) ON CONFLICT (proof_hash) DO NOTHING"
        ),
        {
            "h": digest,
            "s": source_id,
            "u": reading.register_url,
            "b": reading.body,
            "f": reading.fetched_at,
        },
    )
    return digest


async def preserved(conn: AsyncConnection, proof_hash: str) -> bytes | None:
    row = (
        await conn.execute(
            text("SELECT body FROM verify.register_response WHERE proof_hash = :h"),
            {"h": proof_hash},
        )
    ).first()
    return bytes(row.body) if row else None


def check_usable(source_id: str, *, environment: str) -> rs.Source:
    source = registry.sources().get(source_id)
    u = rs.usable(
        source,
        environment=environment,
        connector_available=registry.connector(source_id) is not None,
    )
    if not u.usable or source is None:
        raise rs.SourceError(u.reasons[0], ", ".join(u.reasons))
    return source


async def refresh(
    conn: AsyncConnection,
    source_id: str,
    scope: Scope,
    *,
    environment: str,
    now: datetime,
    recorded_by: str,
) -> Refresh:
    source = check_usable(source_id, environment=environment)
    conn_ = registry.connector(source_id)
    assert conn_ is not None  # checked by check_usable
    reading = await conn_.read(scope, now=now)
    if reading is None:
        return Refresh(False, None, await attestation_store.decide_scope(conn, scope, at=now))
    if reading.scope != scope:
        raise rs.SourceError(
            "SOURCE_SCOPE_MISMATCH", "the connector answered for a different scope"
        )
    digest = await _preserve(conn, source_id, reading)
    snapshot = rs.to_snapshot(
        reading,
        source,
        snapshot_id=f"att-{uuid.uuid4().hex}",
        evidence_locator=f"register-response:{digest}",
        recorded_by=recorded_by,
        recorded_at=now,
    )
    saved = await attestation_store.record(conn, snapshot, now=now)
    return Refresh(True, saved, await attestation_store.decide_scope(conn, scope, at=now))
