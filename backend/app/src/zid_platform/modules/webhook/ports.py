"""Public interface of the webhook module. Other modules import ONLY this."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.webhook.application.runtime import http_client, system_resolver
from zid_platform.modules.webhook.domain.webhooks import SUBSCRIBABLE
from zid_platform.modules.webhook.infrastructure.delivery import deliver_due, route

__all__ = ["SUBSCRIBABLE", "deliver_due", "http_client", "route", "system_resolver"]


async def searchable_endpoints(conn: AsyncConnection) -> list[tuple[str, str, str]]:
    """(id, url, hint) of the tenant's webhook endpoints, for authorization-aware search."""
    from zid_platform.modules.webhook.infrastructure.endpoint_store import list_endpoints

    return [
        (e["endpoint_id"], e["url"], f"webhook · {e['status']}") for e in await list_endpoints(conn)
    ]


async def endpoint_is_active(conn: AsyncConnection, endpoint_id: str) -> bool:
    """Is this webhook endpoint one of the tenant's own and ACTIVE? A callback is only ever a pre-authorized destination."""
    from zid_platform.modules.webhook.domain.webhooks import WebhookError
    from zid_platform.modules.webhook.infrastructure.endpoint_store import load_endpoint

    try:
        return str((await load_endpoint(conn, endpoint_id))["status"]) == "ACTIVE"
    except (WebhookError, ValueError):
        return False


async def active_endpoint_count(conn: AsyncConnection) -> int:
    """How many of the tenant's webhook endpoints are verified and ACTIVE (a Verify API company needs one before it can go live)."""
    from zid_platform.modules.webhook.infrastructure.endpoint_store import list_endpoints

    return sum(1 for e in await list_endpoints(conn) if str(e["status"]) == "ACTIVE")


async def delivered_event(conn: AsyncConnection, event_id: str) -> bool:
    """Did the platform really deliver this event to one of the tenant's own endpoints? (RLS keeps it to the tenant.) A partner's confirmation must name one."""
    import uuid

    from sqlalchemy import text

    try:
        eid = uuid.UUID(event_id)
    except ValueError:
        return False
    row = (
        await conn.execute(
            text(
                "SELECT 1 FROM events.webhook_delivery WHERE event_id = :e AND status = 'DELIVERED' LIMIT 1"
            ),
            {"e": eid},
        )
    ).first()
    return row is not None


async def recent_deliveries_by_endpoint(
    conn: AsyncConnection, per_endpoint: int = 20
) -> list[dict[str, object]]:
    """The newest deliveries of each of the tenant's endpoints (RLS keeps it to the tenant): status, the endpoint's HTTP answer, and the recorded error. No payload and no URL path."""
    from sqlalchemy import text

    rows = (
        (
            await conn.execute(
                text(
                    "SELECT * FROM (SELECT d.endpoint_id, e.status AS endpoint_status, split_part(split_part(e.url, '://', 2), '/', 1) AS host, d.status, d.last_status_code, d.last_error, d.attempts, d.created_at, "
                    "row_number() OVER (PARTITION BY d.endpoint_id ORDER BY d.created_at DESC) AS n FROM events.webhook_delivery d JOIN events.webhook_endpoint e USING (endpoint_id)) x WHERE n <= :k ORDER BY endpoint_id, created_at DESC"
                ),
                {"k": per_endpoint},
            )
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


async def deliveries_for_session(conn: AsyncConnection, session_id: str) -> list[dict[str, object]]:
    """The tenant's webhook deliveries whose event is about this verification session (ids, type, status and times only)."""
    from sqlalchemy import text

    rows = (
        (
            await conn.execute(
                text(
                    "SELECT event_id, event_type, status, last_status_code, last_error, attempts, created_at, delivered_at, replay_of IS NOT NULL AS is_replay "
                    "FROM events.webhook_delivery WHERE payload -> 'data' ->> 'session_id' = :s ORDER BY created_at, delivery_id"
                ),
                {"s": session_id},
            )
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]
