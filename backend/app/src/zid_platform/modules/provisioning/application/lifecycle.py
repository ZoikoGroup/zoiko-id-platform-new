"""HR / workforce lifecycle events from a provisioning source (ZID-OVERLAY-001 §10.1, §22; OVL-09,
OVL-28; G-W5).

An HRIS (or the SCIM source acting for it) reports what happened to a provisioned person; the
platform applies the authority rule for each, never more:

    JOINER       a MEMBER tenant membership (a relationship candidate) - no authority at all
    MOVER        department/manager/job changed: security revision++, so every session and every
                 protected decision re-reads current state; role-dependent access is recalculated
    LEAVER       AUTHORITATIVE account.disabled (principal suspended, sessions revoked, priority
                 lane) and the PERSON_BOUND grants the person issued are revoked (cascade)
    LEAVE_START  temporary suspension without deleting identity (account.disabled)
    LEAVE_END    restored (account.enabled); old sessions never come back

Each event is idempotent per (source, resource, event, effective time) through the signal ledger's
dedupe key and is evidenced (`provisioning.lifecycle.changed`).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from enum import StrEnum

from sqlalchemy.ext.asyncio import AsyncConnection
from zid_outbox import emit

from zid_platform.modules.authority.ports import on_person_authority_ended
from zid_platform.modules.principal.ports import bump_security_revision
from zid_platform.modules.provisioning.infrastructure import scim_store as store
from zid_platform.modules.provisioning.infrastructure.scim_store import Resource, ScimError, Source
from zid_platform.modules.signals.ports import CanonicalSignal, ingest
from zid_platform.modules.tenant.ports import ensure_member


class LifecycleEvent(StrEnum):
    JOINER = "JOINER"
    MOVER = "MOVER"
    LEAVER = "LEAVER"
    LEAVE_START = "LEAVE_START"
    LEAVE_END = "LEAVE_END"


async def _signal(
    conn: AsyncConnection, src: Source, principal: str, key: str, event_type: str, at: datetime
) -> None:
    await ingest(
        conn,
        CanonicalSignal(
            tenant_id=src.tenant_id,
            subject_type="PRINCIPAL",
            subject_ref=principal,
            source_id=f"hris:{src.source_id}",
            source_event_id=key,
            source_event_type="hris:lifecycle",
            event_type=event_type,
            effective_at=at,
            observed_at=at,
            confidence="AUTHORITATIVE",
        ),
        now=at,
    )


async def _sync_active(conn: AsyncConnection, r: Resource, active: bool, now: datetime) -> None:
    """Keep the SCIM User's `active` (and version/ETag) in step with what the HR event did to the
    principal, so the source reads back the state it caused."""
    if r.active != active:
        await store.update(
            conn, r, replace(r, active=active, version=r.version + 1, updated_at=now)
        )


async def apply_event(
    conn: AsyncConnection,
    src: Source,
    *,
    user_id: str,
    event: LifecycleEvent,
    effective_at: datetime,
    now: datetime,
) -> dict[str, object]:
    r = await store.load(conn, src.source_id, "User", user_id, lock=True)
    if r.principal_id is None:
        raise ScimError(404, None, "resource not found")
    principal = r.principal_id
    key = f"{r.resource_id}:{event.value}:{effective_at.isoformat()}"
    effects: dict[str, object] = {"event": event.value, "principal_id": principal}
    if event is LifecycleEvent.JOINER:
        effects["membership"] = await ensure_member(conn, src.tenant_id, principal, now=now)
    elif event is LifecycleEvent.MOVER:
        effects["security_revision"] = await bump_security_revision(
            conn, principal, reason="HR_MOVER"
        )
    elif event is LifecycleEvent.LEAVER:
        await _signal(conn, src, principal, key, "account.disabled", effective_at)
        await _sync_active(conn, r, False, now)
        revoked = await on_person_authority_ended(conn, principal)
        effects["grants_revoked"] = len(revoked)
    elif event is LifecycleEvent.LEAVE_START:
        await _signal(conn, src, principal, key, "account.disabled", effective_at)
        await _sync_active(conn, r, False, now)
    else:
        await _signal(conn, src, principal, key, "account.enabled", effective_at)
        await _sync_active(conn, r, True, now)
    await emit(
        conn,
        name="provisioning.lifecycle.changed",
        tenant_id=src.tenant_id,
        aggregate_type="provisioning_lifecycle",
        aggregate_id=r.resource_id,
        aggregate_version=int(now.timestamp() * 1_000_000),
        data={
            "source_id": src.source_id,
            "resource_id": r.resource_id,
            "principal_id": principal,
            "event": event.value,
            "effective_at": effective_at.isoformat(),
        },
    )
    return effects
