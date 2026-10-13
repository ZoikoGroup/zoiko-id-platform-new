"""Security signals, security state and SSF streams in PostgreSQL (ZID-DATA-001 §10.1).

Inside the caller's tenant transaction (RLS). The state row is read FOR UPDATE before the rule
decision so two signals about one subject are applied one after the other; the signal row's
unique dedupe key makes a replayed event a no-op.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.signals.domain.rules import CurrentState, State


def sha256(data: bytes | str) -> bytes:
    return hashlib.sha256(data.encode() if isinstance(data, str) else data).digest()


async def lock_state(
    conn: AsyncConnection, subject_type: str, subject_id: str
) -> CurrentState | None:
    row = (
        await conn.execute(
            text(
                "SELECT state, revision, effective_at, committed_at FROM signals.security_state "
                "WHERE subject_type = :t AND subject_id = :s FOR UPDATE"
            ),
            {"t": subject_type, "s": subject_id},
        )
    ).first()
    if row is None:
        return None
    return CurrentState(State(row.state), row.revision, row.effective_at, row.committed_at)


async def read_state(
    conn: AsyncConnection, subject_type: str, subject_id: str
) -> CurrentState | None:
    row = (
        await conn.execute(
            text(
                "SELECT state, revision, effective_at, committed_at FROM signals.security_state "
                "WHERE subject_type = :t AND subject_id = :s"
            ),
            {"t": subject_type, "s": subject_id},
        )
    ).first()
    if row is None:
        return None
    return CurrentState(State(row.state), row.revision, row.effective_at, row.committed_at)


async def already_seen(conn: AsyncConnection, dedupe_key: bytes) -> bool:
    row = (
        await conn.execute(
            text("SELECT 1 FROM signals.security_signal WHERE dedupe_key_hash = :d"),
            {"d": dedupe_key},
        )
    ).first()
    return row is not None


@dataclass(frozen=True, slots=True)
class SignalRow:
    signal_id: str
    tenant_id: str
    subject_type: str
    subject_ref: str
    source_id: str
    source_event_id: str
    source_event_type: str
    event_type: str
    severity: str
    confidence: str
    required_action: str
    risk_class_floor: int
    effective_at: datetime
    observed_at: datetime
    normalized_at: datetime
    dedupe_key_hash: bytes
    payload_commitment: bytes | None
    source_subject_commitment: bytes | None
    status: str
    reason_code: str
    state_revision: int | None


async def insert_signal(conn: AsyncConnection, s: SignalRow) -> bool:
    """False when the dedupe key was already recorded (concurrent duplicate)."""
    result = await conn.execute(
        text(
            "INSERT INTO signals.security_signal (signal_id, tenant_id, subject_type, subject_ref, "
            "source_id, source_event_id, source_event_type, event_type, severity, confidence, "
            "required_action, risk_class_floor, effective_at, observed_at, normalized_at, "
            "dedupe_key_hash, payload_commitment, source_subject_commitment, status, reason_code, "
            "state_revision) VALUES (:id, :t, :st, :sr, :src, :seid, :set, :et, :sev, :conf, :ra, "
            ":rf, :eff, :obs, :norm, :dd, :pc, :ssc, :status, :reason, :rev) "
            "ON CONFLICT (tenant_id, dedupe_key_hash) DO NOTHING"
        ),
        {
            "id": uuid.UUID(s.signal_id),
            "t": uuid.UUID(s.tenant_id),
            "st": s.subject_type,
            "sr": s.subject_ref,
            "src": s.source_id,
            "seid": s.source_event_id,
            "set": s.source_event_type,
            "et": s.event_type,
            "sev": s.severity,
            "conf": s.confidence,
            "ra": s.required_action,
            "rf": s.risk_class_floor,
            "eff": s.effective_at,
            "obs": s.observed_at,
            "norm": s.normalized_at,
            "dd": s.dedupe_key_hash,
            "pc": s.payload_commitment,
            "ssc": s.source_subject_commitment,
            "status": s.status,
            "reason": s.reason_code,
            "rev": s.state_revision,
        },
    )
    return result.rowcount == 1


async def write_state(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    subject_type: str,
    subject_id: str,
    state: State,
    previous: CurrentState | None,
    effective_at: datetime,
    now: datetime,
    reason: str,
    cause_signal_id: str,
) -> int:
    revision = (previous.revision if previous else 0) + 1
    effective = max(effective_at, previous.effective_at) if previous else effective_at
    if previous is None:
        await conn.execute(
            text(
                "INSERT INTO signals.security_state (tenant_id, subject_type, subject_id, state, "
                "revision, effective_at, committed_at, reason_code, cause_signal_id) "
                "VALUES (:t, :st, :s, :state, :rev, :eff, :now, :r, :c)"
            ),
            {
                "t": uuid.UUID(tenant_id),
                "st": subject_type,
                "s": subject_id,
                "state": state.value,
                "rev": revision,
                "eff": effective,
                "now": now,
                "r": reason,
                "c": uuid.UUID(cause_signal_id),
            },
        )
    else:
        await conn.execute(
            text(
                "UPDATE signals.security_state SET state = :state, revision = :rev, "
                "effective_at = :eff, committed_at = :now, reason_code = :r, cause_signal_id = :c "
                "WHERE subject_type = :st AND subject_id = :s"
            ),
            {
                "st": subject_type,
                "s": subject_id,
                "state": state.value,
                "rev": revision,
                "eff": effective,
                "now": now,
                "r": reason,
                "c": uuid.UUID(cause_signal_id),
            },
        )
    return revision


@dataclass(frozen=True, slots=True)
class Stream:
    stream_id: str
    tenant_id: str
    direction: str
    issuer: str
    audience: str
    jwks: dict[str, Any] | None
    algorithms: tuple[str, ...]
    events: tuple[str, ...]
    subject_formats: tuple[str, ...]
    delivery_method: str
    push_token_hash: bytes | None
    source_trust: str
    federation_issuer: str | None
    receiver_client_id: str | None
    status: str
    metadata_revision: int


_STREAM_COLS = (
    "stream_id, tenant_id, direction, issuer, audience, jwks, algorithms, events, subject_formats, "
    "delivery_method, push_token_hash, source_trust, federation_issuer, receiver_client_id, status, "
    "metadata_revision"
)


def _stream(r: object) -> Stream:
    m = r._mapping  # type: ignore[attr-defined]
    return Stream(
        stream_id=str(m["stream_id"]),
        tenant_id=str(m["tenant_id"]),
        direction=m["direction"],
        issuer=m["issuer"],
        audience=m["audience"],
        jwks=m["jwks"],
        algorithms=tuple(m["algorithms"]),
        events=tuple(m["events"]),
        subject_formats=tuple(m["subject_formats"]),
        delivery_method=m["delivery_method"],
        push_token_hash=bytes(m["push_token_hash"]) if m["push_token_hash"] else None,
        source_trust=m["source_trust"],
        federation_issuer=m["federation_issuer"],
        receiver_client_id=m["receiver_client_id"],
        status=m["status"],
        metadata_revision=m["metadata_revision"],
    )


async def create_stream(conn: AsyncConnection, s: Stream) -> None:
    await conn.execute(
        text(
            f"INSERT INTO signals.ssf_stream ({_STREAM_COLS}) VALUES (:id, :t, :dir, :iss, :aud, "
            "CAST(:jwks AS jsonb), :algs, :events, :fmts, :dm, :pth, :trust, :fiss, :rcid, :status, "
            ":rev)"
        ),
        {
            "id": uuid.UUID(s.stream_id),
            "t": uuid.UUID(s.tenant_id),
            "dir": s.direction,
            "iss": s.issuer,
            "aud": s.audience,
            "jwks": json.dumps(s.jwks) if s.jwks is not None else None,
            "algs": list(s.algorithms),
            "events": list(s.events),
            "fmts": list(s.subject_formats),
            "dm": s.delivery_method,
            "pth": s.push_token_hash,
            "trust": s.source_trust,
            "fiss": s.federation_issuer,
            "rcid": s.receiver_client_id,
            "status": s.status,
            "rev": s.metadata_revision,
        },
    )


async def load_stream(conn: AsyncConnection, stream_id: str) -> Stream | None:
    try:
        sid = uuid.UUID(stream_id)
    except ValueError:
        return None
    row = (
        await conn.execute(
            text(f"SELECT {_STREAM_COLS} FROM signals.ssf_stream WHERE stream_id = :s"),
            {"s": sid},
        )
    ).first()
    return _stream(row) if row else None


async def set_stream_status(conn: AsyncConnection, stream_id: str, status: str) -> bool:
    result = await conn.execute(
        text(
            "UPDATE signals.ssf_stream SET status = :st, updated_at = now() "
            "WHERE stream_id = :s AND status <> 'RETIRED'"
        ),
        {"s": uuid.UUID(stream_id), "st": status},
    )
    return result.rowcount == 1


async def transmitter_streams(conn: AsyncConnection, event_uri: str) -> list[Stream]:
    rows = (
        await conn.execute(
            text(
                f"SELECT {_STREAM_COLS} FROM signals.ssf_stream WHERE direction = 'TRANSMITTER' "
                "AND status = 'ACTIVE' AND :e = ANY(events)"
            ),
            {"e": event_uri},
        )
    ).all()
    return [_stream(r) for r in rows]


async def map_subject(
    conn: AsyncConnection, *, stream_id: str, tenant_id: str, opaque_id: str, principal_id: str
) -> None:
    await conn.execute(
        text(
            "INSERT INTO signals.ssf_subject_map (stream_id, opaque_id, tenant_id, principal_id) "
            "VALUES (:s, :o, :t, :p) ON CONFLICT DO NOTHING"
        ),
        {
            "s": uuid.UUID(stream_id),
            "o": opaque_id,
            "t": uuid.UUID(tenant_id),
            "p": uuid.UUID(principal_id),
        },
    )


async def opaque_principal(conn: AsyncConnection, stream_id: str, opaque_id: str) -> str | None:
    row = (
        await conn.execute(
            text(
                "SELECT principal_id FROM signals.ssf_subject_map WHERE stream_id = :s "
                "AND opaque_id = :o"
            ),
            {"s": uuid.UUID(stream_id), "o": opaque_id},
        )
    ).first()
    return str(row.principal_id) if row else None


async def opaque_for(conn: AsyncConnection, stream_id: str, principal_id: str) -> str | None:
    row = (
        await conn.execute(
            text(
                "SELECT opaque_id FROM signals.ssf_subject_map WHERE stream_id = :s "
                "AND principal_id = :p"
            ),
            {"s": uuid.UUID(stream_id), "p": uuid.UUID(principal_id)},
        )
    ).first()
    return str(row.opaque_id) if row else None


async def enqueue_set(
    conn: AsyncConnection,
    *,
    stream_id: str,
    tenant_id: str,
    jti: str,
    set_jwt: str,
    committed_at: datetime | None = None,
    risk_floor: int = 1,
) -> None:
    await conn.execute(
        text(
            "INSERT INTO signals.ssf_outbound_set (stream_id, jti, tenant_id, set_jwt, "
            "event_committed_at, risk_floor) VALUES (:s, :j, :t, :jwt, :c, :r) ON CONFLICT DO NOTHING"
        ),
        {
            "s": uuid.UUID(stream_id),
            "j": jti,
            "t": uuid.UUID(tenant_id),
            "jwt": set_jwt,
            "c": committed_at,
            "r": risk_floor,
        },
    )


# FR-SIG-004 cross-party targets (commit -> receiver acknowledgement), by risk floor.
PROPAGATION_SLO_MS = {0: 15 * 60 * 1000, 1: 60_000, 2: 10_000, 3: 5_000}
_LAG = "extract(epoch FROM acked_at - coalesce(event_committed_at, created_at)) * 1000"


async def propagation(conn: AsyncConnection, stream_id: str, now: datetime) -> dict[str, object]:
    """Commit -> receiver acknowledgement latency for one stream (FR-SIG-003)."""
    row = (
        await conn.execute(
            text(
                "SELECT count(*) FILTER (WHERE acked_at IS NOT NULL) AS acked, "
                "count(*) FILTER (WHERE acked_at IS NULL) AS pending, "
                f"percentile_cont(0.5) WITHIN GROUP (ORDER BY {_LAG}) AS p50, "
                f"percentile_cont(0.99) WITHIN GROUP (ORDER BY {_LAG}) AS p99, "
                f"max({_LAG}) AS max_ms, "
                "max(extract(epoch FROM CAST(:now AS timestamptz) - "
                "coalesce(event_committed_at, created_at)) * 1000) "
                "FILTER (WHERE acked_at IS NULL) AS oldest_pending_ms, "
                f"count(*) FILTER (WHERE acked_at IS NOT NULL AND {_LAG} > CASE risk_floor "
                "WHEN 3 THEN 5000 WHEN 2 THEN 10000 WHEN 1 THEN 60000 ELSE 900000 END) "
                "AS slo_breaches FROM signals.ssf_outbound_set WHERE stream_id = :s"
            ),
            {"s": uuid.UUID(stream_id), "now": now},
        )
    ).first()
    m: dict[str, Any] = dict(row._mapping) if row else {}

    def ms(key: str) -> float | None:
        return round(float(m[key]), 1) if m.get(key) is not None else None

    return {
        "acknowledged": int(m.get("acked") or 0),
        "pending": int(m.get("pending") or 0),
        "p50_ms": ms("p50"),
        "p99_ms": ms("p99"),
        "max_ms": ms("max_ms"),
        "oldest_pending_ms": ms("oldest_pending_ms"),
        "slo_breaches": int(m.get("slo_breaches") or 0),
        "slo_ms_by_risk": PROPAGATION_SLO_MS,
    }


async def poll(
    conn: AsyncConnection, stream_id: str, *, ack: list[str], max_events: int, now: datetime
) -> list[tuple[str, str]]:
    """RFC 8936: acknowledge, then hand back up to `max_events` unacknowledged SETs."""
    if ack:
        await conn.execute(
            text(
                "UPDATE signals.ssf_outbound_set SET acked_at = :now WHERE stream_id = :s "
                "AND jti = ANY(:j) AND acked_at IS NULL"
            ),
            {"s": uuid.UUID(stream_id), "j": ack, "now": now},
        )
    rows = (
        await conn.execute(
            text(
                "SELECT jti, set_jwt FROM signals.ssf_outbound_set WHERE stream_id = :s "
                "AND acked_at IS NULL ORDER BY created_at LIMIT :n"
            ),
            {"s": uuid.UUID(stream_id), "n": max_events},
        )
    ).all()
    return [(r.jti, r.set_jwt) for r in rows]
