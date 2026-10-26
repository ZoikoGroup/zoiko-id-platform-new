"""Alert storage (tenant transactions, RLS on). `platform.notification_request`, owner: notification."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection
from zid_context import uuid7

from zid_platform.modules.notification.domain.alerts import AlertDraft, AlertError, AlertState

_COLUMNS = (
    "alert_id, severity, kind, title, body, object_type, object_ref, href, recommended_action, is_task, "
    "mandatory, state, occurrences, first_seen_at, last_seen_at, acknowledged_by, acknowledged_at, "
    "resolved_by, resolved_at"
)
_SELECT = f"SELECT {_COLUMNS} FROM platform.notification_request"
_SEVERITY_ORDER = (
    "CASE severity WHEN 'CRITICAL' THEN 0 WHEN 'HIGH' THEN 1 WHEN 'MEDIUM' THEN 2 ELSE 3 END"
)


def _iso(v: Any) -> str | None:
    return v.isoformat() if v is not None else None


def _alert(r: Any) -> dict[str, Any]:
    return {
        "alert_id": str(r.alert_id),
        "severity": r.severity,
        "kind": r.kind,
        "title": r.title,
        "body": r.body,
        "object_type": r.object_type,
        "object_ref": r.object_ref,
        "href": r.href,
        "recommended_action": r.recommended_action,
        "is_task": r.is_task,
        "mandatory": r.mandatory,
        "state": r.state,
        "occurrences": r.occurrences,
        "first_seen_at": _iso(r.first_seen_at),
        "last_seen_at": _iso(r.last_seen_at),
        "acknowledged_by": str(r.acknowledged_by) if r.acknowledged_by else None,
        "acknowledged_at": _iso(r.acknowledged_at),
        "resolved_by": str(r.resolved_by) if r.resolved_by else None,
        "resolved_at": _iso(r.resolved_at),
    }


async def raise_alert(
    conn: AsyncConnection, tenant_id: str, draft: AlertDraft, *, now: datetime
) -> tuple[str, bool]:
    """Insert, or fold a correlated repeat into the live alert. Returns (alert_id, created)."""
    row = (
        await conn.execute(
            text(
                "INSERT INTO platform.notification_request (alert_id, tenant_id, severity, kind, title, body, "
                "object_type, object_ref, href, recommended_action, is_task, mandatory, correlation_key, "
                "source_event_id, first_seen_at, last_seen_at) VALUES (:id, :t, :sev, :kind, :title, :body, "
                ":ot, :oref, :href, :rec, :task, :mand, :ck, :ev, :now, :now) "
                "ON CONFLICT (tenant_id, correlation_key) WHERE state <> 'RESOLVED' DO UPDATE SET "
                "occurrences = platform.notification_request.occurrences + 1, last_seen_at = EXCLUDED.last_seen_at, "
                "source_event_id = EXCLUDED.source_event_id "
                "RETURNING alert_id, (xmax = 0) AS created"
            ),
            {
                "id": uuid7(),
                "t": uuid.UUID(tenant_id),
                "sev": draft.severity.value,
                "kind": draft.kind,
                "title": draft.title,
                "body": draft.body,
                "ot": draft.object_type,
                "oref": draft.object_ref,
                "href": draft.href,
                "rec": draft.recommended_action,
                "task": draft.is_task,
                "mand": draft.mandatory,
                "ck": draft.correlation_key,
                "ev": uuid.UUID(draft.source_event_id) if draft.source_event_id else None,
                "now": now,
            },
        )
    ).one()
    return str(row.alert_id), bool(row.created)


async def list_alerts(
    conn: AsyncConnection,
    *,
    state: str | None = None,
    severity: str | None = None,
    tasks_only: bool = False,
    limit: int = 200,
) -> list[dict[str, Any]]:
    where: list[str] = []
    params: dict[str, Any] = {"n": limit}
    if state:
        where.append("state = :state")
        params["state"] = state
    else:
        where.append("state <> 'RESOLVED'")
    if severity:
        where.append("severity = :sev")
        params["sev"] = severity
    if tasks_only:
        where.append("is_task")
    rows = (
        await conn.execute(
            text(
                f"{_SELECT} WHERE {' AND '.join(where)} ORDER BY {_SEVERITY_ORDER}, last_seen_at DESC LIMIT :n"
            ),
            params,
        )
    ).all()
    return [_alert(r) for r in rows]


async def load_alert(conn: AsyncConnection, alert_id: str) -> dict[str, Any]:
    row = (
        await conn.execute(text(f"{_SELECT} WHERE alert_id = :a"), {"a": uuid.UUID(alert_id)})
    ).first()
    if row is None:
        raise AlertError("ALERT_NOT_FOUND")
    return _alert(row)


async def summary(conn: AsyncConnection) -> dict[str, Any]:
    rows = (
        await conn.execute(
            text(
                "SELECT severity, state, count(*) AS n FROM platform.notification_request "
                "WHERE state <> 'RESOLVED' GROUP BY severity, state"
            )
        )
    ).all()
    by_severity = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "INFO": 0}
    unacknowledged = 0
    for r in rows:
        by_severity[r.severity] += r.n
        if r.state == AlertState.OPEN.value:
            unacknowledged += r.n
    return {
        "open": sum(by_severity.values()),
        "unacknowledged": unacknowledged,
        "by_severity": by_severity,
    }


async def move(
    conn: AsyncConnection, alert_id: str, *, to: AlertState, actor: str, now: datetime
) -> dict[str, Any]:
    """Row-locked transition; the caller has already checked the transition rules."""
    column = "acknowledged" if to is AlertState.ACKNOWLEDGED else "resolved"
    await conn.execute(
        text(
            f"UPDATE platform.notification_request SET state = :s, {column}_by = :by, {column}_at = :now "
            "WHERE alert_id = :a"
        ),
        {"s": to.value, "by": uuid.UUID(actor), "now": now, "a": uuid.UUID(alert_id)},
    )
    return await load_alert(conn, alert_id)
