"""Apply one canonical security signal (ZID-SIGNALS-001 §1.1, §13-16, §22, §27).

    validated signal -> dedupe -> rule -> consequences -> state + revision -> security.state.changed

Everything happens in the caller's tenant transaction, so the state change, its consequences
(sessions revoked, principal suspended, revision bumped, link severed, authenticator revoked), the
append-only signal record and the propagation event commit together or not at all. The signal row
is written *before* the consequences: a concurrent duplicate of the same event loses the unique
dedupe key and leaves without touching anything (SIG-05).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncConnection
from zid_context import uuid7
from zid_outbox import emit

from zid_platform.modules.authentication.ports import (
    find_external_link,
    revoke_external_link,
    revoke_for_security,
)
from zid_platform.modules.principal.ports import (
    PrincipalStatus,
    bump_security_revision,
    load_principal,
    set_status,
)
from zid_platform.modules.session.ports import (
    SessionStatus,
    load_session,
    revoke_principal_sessions,
    set_session_status,
)
from zid_platform.modules.signals.application.state_cache import CACHE
from zid_platform.modules.signals.domain.rules import (
    RULE_RELEASE,
    RULES,
    Rule,
    SignalError,
    State,
    evaluate,
)
from zid_platform.modules.signals.infrastructure import signal_store as store

SUBJECTS = frozenset({"PRINCIPAL", "SESSION", "CREDENTIAL"})


@dataclass(frozen=True, slots=True)
class CanonicalSignal:
    tenant_id: str
    subject_type: (
        str  # PRINCIPAL | SESSION | CREDENTIAL (others are rejected until their domain lands)
    )
    subject_ref: str  # canonical opaque id after subject resolution
    source_id: str  # stream / provider / internal source
    source_event_id: str  # SET jti or provider event id: the dedupe key
    source_event_type: str
    event_type: str  # canonical (rules.RULES)
    effective_at: datetime
    observed_at: datetime
    confidence: str = "HIGH"
    principal_id: str | None = None  # owner, for SESSION / CREDENTIAL subjects
    external_link: tuple[str, str] | None = None  # (issuer, subject) the signal is about
    payload_commitment: bytes | None = None
    source_subject_commitment: bytes | None = None


@dataclass(frozen=True, slots=True)
class IngestResult:
    signal_id: str
    status: str  # APPLIED | OUT_OF_ORDER | INFORMATIONAL | DUPLICATE
    state: str | None
    revision: int | None
    reason: str


async def ingest(conn: AsyncConnection, s: CanonicalSignal, *, now: datetime) -> IngestResult:
    rule = RULES.get(s.event_type)
    if rule is None:
        raise SignalError("SIGNAL_EVENT_NOT_ALLOWED", s.event_type)
    if s.subject_type not in SUBJECTS:
        raise SignalError("SIGNAL_SUBJECT_INVALID", s.subject_type)
    dedupe = store.sha256(f"{s.source_id}|{s.source_event_id}")
    if await store.already_seen(conn, dedupe):
        return IngestResult("", "DUPLICATE", None, None, "SIGNAL_REPLAY")
    current = await store.lock_state(conn, s.subject_type, s.subject_ref)
    outcome = evaluate(rule, current, effective_at=s.effective_at, confidence=s.confidence)
    revision = (current.revision if current else 0) + 1 if outcome.new_state else None
    signal_id = str(uuid7())
    recorded = await store.insert_signal(
        conn,
        store.SignalRow(
            signal_id=signal_id,
            tenant_id=s.tenant_id,
            subject_type=s.subject_type,
            subject_ref=s.subject_ref,
            source_id=s.source_id,
            source_event_id=s.source_event_id,
            source_event_type=s.source_event_type,
            event_type=s.event_type,
            severity=rule.severity,
            confidence=s.confidence,
            required_action=rule.action.value,
            risk_class_floor=rule.risk_floor,
            effective_at=s.effective_at,
            observed_at=s.observed_at,
            normalized_at=now,
            dedupe_key_hash=dedupe,
            payload_commitment=s.payload_commitment,
            source_subject_commitment=s.source_subject_commitment,
            status=outcome.status,
            reason_code=outcome.reason,
            state_revision=revision,
        ),
    )
    if not recorded:
        return IngestResult("", "DUPLICATE", None, None, "SIGNAL_REPLAY")
    if outcome.status != "APPLIED":
        return IngestResult(signal_id, outcome.status, None, None, outcome.reason)
    await _consequences(conn, s, rule, now)
    if outcome.new_state is None:
        return IngestResult(signal_id, "APPLIED", None, None, outcome.reason)
    revision = await store.write_state(
        conn,
        tenant_id=s.tenant_id,
        subject_type=s.subject_type,
        subject_id=s.subject_ref,
        state=outcome.new_state,
        previous=current,
        effective_at=s.effective_at,
        now=now,
        reason=outcome.reason,
        cause_signal_id=signal_id,
    )
    CACHE.invalidate(s.tenant_id, s.subject_ref, revision)
    await emit(
        conn,
        name="security.state.changed",
        tenant_id=s.tenant_id,
        aggregate_type=f"security_state.{s.subject_type.lower()}",
        aggregate_id=s.subject_ref,
        aggregate_version=revision,
        data={
            "subject": {"type": s.subject_type, "id": s.subject_ref},
            "cause_signal_id": signal_id,
            "previous_state": current.state.value if current else State.TRUSTED.value,
            "current_state": outcome.new_state.value,
            "security_revision": revision,
            "effective_at": s.effective_at.isoformat(),
            "committed_at": now.isoformat(),
            "reason_code": outcome.reason,
            "risk_floor": f"R{rule.risk_floor}",
            "rule_release": RULE_RELEASE,
        },
    )
    return IngestResult(signal_id, "APPLIED", outcome.new_state.value, revision, outcome.reason)


async def _consequences(
    conn: AsyncConnection, s: CanonicalSignal, rule: Rule, now: datetime
) -> None:
    reason = f"signal:{rule.reason}"
    if s.subject_type == "SESSION":
        session = await load_session(conn, s.subject_ref)
        if session is not None and session.status in (
            SessionStatus.ACTIVE,
            SessionStatus.REAUTH_REQUIRED,
        ):
            await set_session_status(conn, s.subject_ref, SessionStatus.REVOKED, reason=reason)
        return
    if s.subject_type == "CREDENTIAL":
        if rule.revoke_credential or rule.revoke_sessions:
            await revoke_for_security(conn, authenticator_id=s.subject_ref, reason=reason, now=now)
        return
    principal_id = s.subject_ref
    try:
        principal, _ = await load_principal(conn, principal_id)
    except ValueError as e:
        raise SignalError("SIGNAL_SUBJECT_UNRESOLVED") from e
    if rule.suspend_principal and principal.status is PrincipalStatus.ACTIVE:
        await set_status(conn, principal_id, PrincipalStatus.SUSPENDED)
    elif rule.restores and principal.status is PrincipalStatus.SUSPENDED:
        await set_status(conn, principal_id, PrincipalStatus.ACTIVE)  # sessions stay revoked
    if rule.bump_revision:
        await bump_security_revision(conn, principal_id, reason=reason)
    if rule.revoke_sessions:
        await revoke_principal_sessions(conn, principal_id, reason=reason)
    if rule.sever_link and s.external_link is not None:
        link = await find_external_link(conn, *s.external_link)
        if link is not None and link.principal_id == principal_id:
            await revoke_external_link(conn, link, reason=reason, now=now)


def commitment(value: str) -> bytes:
    return store.sha256(value)
