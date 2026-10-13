"""Security state reads and the governed override (ZID-SIGNALS-001 §2, §14, §26).

`pdp_security_state` is the PIP the decision path uses: in-cell, read in the same database the
signals are applied to, so it is never older than the last committed signal. A governed override
(after investigation) is itself recorded as an append-only signal with its own revision.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncConnection
from zid_context import uuid7
from zid_outbox import emit
from zid_telemetry import get_logger

from zid_platform.modules.signals.domain.rules import TERMINAL, State, pdp_state
from zid_platform.modules.signals.infrastructure import signal_store as store

_log = get_logger("zid.signals.propagation")


async def pdp_security_state(
    conn: AsyncConnection, principal_id: str, *, auth_time: datetime | None
) -> str:
    """In-cell PEPs read the authoritative state synchronously (no cache): a committed change is
    enforced on the next request. Each observation of a restricting state is logged with its
    commit -> observation lag (FR-SIG-003)."""
    current = await store.read_state(conn, "PRINCIPAL", principal_id)
    state = pdp_state(current, auth_time=auth_time)
    if current is not None and state != "NORMAL":
        lag_ms = (datetime.now(UTC) - current.committed_at).total_seconds() * 1000
        _log.info(
            "security state observed by PEP",
            extra={
                "component": "signals.propagation",
                "subject": principal_id,
                "state": state,
                "revision": current.revision,
                "commit_to_observation_ms": round(lag_ms, 1),
            },
        )
    return state


async def read(conn: AsyncConnection, subject_type: str, subject_id: str) -> dict[str, object]:
    current = await store.read_state(conn, subject_type, subject_id)
    if current is None:
        return {
            "subject_type": subject_type,
            "subject_id": subject_id,
            "state": "TRUSTED",
            "revision": 0,
        }
    return {
        "subject_type": subject_type,
        "subject_id": subject_id,
        "state": current.state.value,
        "revision": current.revision,
        "effective_at": current.effective_at.isoformat(),
        "committed_at": current.committed_at.isoformat(),
    }


async def override(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    subject_type: str,
    subject_id: str,
    target: State,
    by_principal: str,
    reason: str,
    now: datetime,
) -> int:
    """Governed re-evaluation: may set any non-terminal state, recorded like any other signal."""
    current = await store.lock_state(conn, subject_type, subject_id)
    if current is not None and current.state in TERMINAL:
        raise ValueError("SECURITY_STATE_TERMINAL")
    signal_id = str(uuid7())
    revision = (current.revision if current else 0) + 1
    await store.insert_signal(
        conn,
        store.SignalRow(
            signal_id=signal_id,
            tenant_id=tenant_id,
            subject_type=subject_type,
            subject_ref=subject_id,
            source_id=f"admin:{by_principal}",
            source_event_id=signal_id,
            source_event_type="zoiko:security_state.override",
            event_type="security_state.override",
            severity="INFO",
            confidence="AUTHORITATIVE",
            required_action="REEVALUATE",
            risk_class_floor=3,
            effective_at=now,
            observed_at=now,
            normalized_at=now,
            dedupe_key_hash=store.sha256(f"override|{signal_id}"),
            payload_commitment=store.sha256(reason),
            source_subject_commitment=None,
            status="APPLIED",
            reason_code="SECURITY_STATE_OVERRIDE",
            state_revision=revision,
        ),
    )
    written = await store.write_state(
        conn,
        tenant_id=tenant_id,
        subject_type=subject_type,
        subject_id=subject_id,
        state=target,
        previous=current,
        effective_at=now,
        now=now,
        reason="SECURITY_STATE_OVERRIDE",
        cause_signal_id=signal_id,
    )
    await emit(
        conn,
        name="security.state.changed",
        tenant_id=tenant_id,
        aggregate_type=f"security_state.{subject_type.lower()}",
        aggregate_id=subject_id,
        aggregate_version=written,
        data={
            "subject": {"type": subject_type, "id": subject_id},
            "cause_signal_id": signal_id,
            "previous_state": current.state.value if current else "TRUSTED",
            "current_state": target.value,
            "security_revision": written,
            "committed_at": now.isoformat(),
            "reason_code": "SECURITY_STATE_OVERRIDE",
            "risk_floor": "R3",
        },
    )
    return written
