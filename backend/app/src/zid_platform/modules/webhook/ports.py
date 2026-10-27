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
