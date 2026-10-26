"""Public interface of the notification module. Other modules import ONLY this."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.notification.application.service import alert_from_event, raise_alert
from zid_platform.modules.notification.domain.alerts import (
    DOMAINS,
    MANDATORY_KINDS,
    TEMPLATES,
    AlertDraft,
    Severity,
)

__all__ = [
    "DOMAINS",
    "MANDATORY_KINDS",
    "TEMPLATES",
    "AlertDraft",
    "Severity",
    "alert_from_event",
    "raise_alert",
]


async def searchable_alerts(conn: AsyncConnection) -> list[tuple[str, str, str]]:
    """(id, title, hint) of the tenant's live alerts, for authorization-aware search."""
    from zid_platform.modules.notification.infrastructure.alert_store import list_alerts

    return [
        (a["alert_id"], a["title"], f"{a['severity'].lower()} alert · {a['state']}")
        for a in await list_alerts(conn)
    ]
