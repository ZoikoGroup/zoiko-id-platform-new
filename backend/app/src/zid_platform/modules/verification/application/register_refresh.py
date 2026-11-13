"""Refresh a provider's certification from its official register (ZID-RCG-003 RCG-G02; CR-087).

Asks the source's connector about one exact scope, preserves the register's response, and records the attestation snapshot it proves, in the
caller's transaction. A source that may not be used here is refused with its reasons and nothing is asked. A scope the register does not list is
recorded as nothing: with no fresh snapshot the attestation rules put it on HOLD.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

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


REFRESH_AFTER = timedelta(
    days=1
)  # well inside the attestation freshness limit (7 days), so an answer never goes stale while the register is up
SYSTEM_RECORDER = "system:register-refresh"


def _source_for(snapshot: Snapshot, sources: dict[str, rs.Source]) -> str | None:
    """The configured source whose register this snapshot came from: only snapshots a connector recorded are refreshed automatically; one
    an operator copied by hand is refreshed by an operator."""
    if not snapshot.evidence_locator.startswith("register-response:"):
        return None
    for sid, s in sources.items():
        if snapshot.register_url.startswith(s.base_url):
            return sid
    return None


async def refresh_due(
    conn: AsyncConnection,
    *,
    environment: str,
    now: datetime,
    refresh_after: timedelta = REFRESH_AFTER,
    limit: int = 50,
) -> dict[str, int]:
    """Worker pass: ask the register again for every connector-recorded scope whose latest answer is older than `refresh_after`. A source that may
    not be used here, or a register that does not answer, records nothing: the scope then goes to HOLD by the freshness rule, never stays
    certified on an old answer."""
    counts = {"refreshed": 0, "not_listed": 0, "skipped": 0, "failed": 0}
    sources = registry.sources()
    for scope in await attestation_store.scopes_of(conn, limit=500):
        if sum(counts.values()) >= limit:
            break
        history = await attestation_store.history(conn, scope)
        if not history:
            continue
        latest = max(history, key=lambda s: s.fetched_at)
        sid = _source_for(latest, sources)
        if sid is None or now - latest.fetched_at < refresh_after:
            continue
        try:
            async with (
                conn.begin_nested()
            ):  # one register's failure rolls back only its own refresh
                result = await refresh(
                    conn, sid, scope, environment=environment, now=now, recorded_by=SYSTEM_RECORDER
                )
        except rs.SourceError:
            counts["skipped"] += 1
            continue
        except Exception:  # noqa: BLE001  a register that fails must not stop the pass for the others
            counts["failed"] += 1
            continue
        counts["refreshed" if result.listed else "not_listed"] += 1
    return counts
