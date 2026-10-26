"""Alert handling: raising from platform events, acknowledgement and resolution (UX-001 §20).

Acknowledging records that someone has seen an alert; only resolving closes it. Both leave an audit
record naming the actor. Raising an alert is derived state (never evidence of its own), so it writes none.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection
from zid_outbox import CloudEvent

from zid_platform.modules.evidence.ports import AuditFacts, EvidenceClass, record_audit
from zid_platform.modules.notification.domain.alerts import (
    AlertDraft,
    AlertState,
    check_transition,
    draft_for_event,
)
from zid_platform.modules.notification.infrastructure import alert_store as store


async def raise_alert(
    conn: AsyncConnection, tenant_id: str, draft: AlertDraft, *, now: datetime
) -> str:
    alert_id, _created = await store.raise_alert(conn, tenant_id, draft, now=now)
    return alert_id


async def alert_from_event(
    conn: AsyncConnection,
    event: CloudEvent,
    *,
    now: datetime,
    muted: frozenset[str] = frozenset(),
) -> str | None:
    """The consumer path: one alert (or one more occurrence of it) for a catalogued platform event."""
    draft = draft_for_event(
        event.type,
        subject=event.subject,
        aggregate_type=event.aggregatetype,
        aggregate_id=event.aggregateid,
        event_id=event.id,
    )
    if draft is None:
        return None
    if draft.kind in muted and not draft.mandatory:  # mandatory notices can never be muted (UX §20)
        return None
    return await raise_alert(conn, event.tenantid, draft, now=now)


async def change_state(
    conn: AsyncConnection,
    tenant_id: str,
    alert_id: str,
    *,
    to: AlertState,
    actor: str,
    now: datetime,
) -> dict[str, Any]:
    current = await store.load_alert(conn, alert_id)  # 404 before 409
    check_transition(current["state"], to)
    alert = await store.move(conn, alert_id, to=to, actor=actor, now=now)
    await record_audit(
        conn,
        tenant_id,
        AuditFacts(
            event_type=f"notification.alert.{to.value.lower()}",
            actor_type="HUMAN",
            action=to.value.lower(),
            target_type="notification_alert",
            target_ref=alert_id,
            outcome="SUCCESS",
            evidence_class=EvidenceClass.E1,
            occurred_at=now,
            actor_principal_id=actor,
            source_service="notification",
            source_event_id=None,
            details={"severity": alert["severity"], "kind": alert["kind"]},
        ),
    )
    return alert
