"""Global search API (ZID-UX-001 §3.1, §25; CR-020).

GET /v1/search?q=...    authorization-aware, tenant-scoped, grouped by type. A type the caller may not
                        read contributes nothing (no hits, no counts, no hint that it exists).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncConnection
from zid_db import tenant_transaction
from zid_errors import ZidError

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.modules.integration.ports import searchable_applications, searchable_connectors
from zid_platform.modules.notification.ports import searchable_alerts
from zid_platform.modules.search.domain.hits import MAX_QUERY, MIN_QUERY, Hit, clean, rank
from zid_platform.modules.tenant.ports import searchable_domains
from zid_platform.modules.webhook.ports import searchable_endpoints
from zid_platform.protected import protected_action

router = APIRouter(tags=["search"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]


@dataclass(frozen=True, slots=True)
class Provider:
    type: str
    label: str
    action: str
    resource: str
    href: str
    rows: Callable[[AsyncConnection], Awaitable[list[tuple[str, str, str]]]]


PROVIDERS = (
    Provider(
        "application",
        "Applications",
        "integration.read",
        "integration_application:*",
        "/admin/applications",
        searchable_applications,
    ),
    Provider(
        "connector",
        "Connectors",
        "integration.read",
        "integration_connector:*",
        "/admin/integrations",
        searchable_connectors,
    ),
    Provider(
        "domain",
        "Domains",
        "tenant.domain.read",
        "tenant_domain:*",
        "/admin/domains",
        searchable_domains,
    ),
    Provider(
        "webhook",
        "Webhook endpoints",
        "webhook.endpoint.read",
        "webhook_endpoint:*",
        "/admin/developers",
        searchable_endpoints,
    ),
    Provider("alert", "Alerts", "notification.alert.read", "alert:*", "/alerts", searchable_alerts),
)


@router.get("/v1/search")
async def search(
    request: Request,
    caller: CallerDep,
    q: Annotated[str, Query(max_length=200)],
) -> dict[str, Any]:
    query = clean(q)
    if len(query) < MIN_QUERY:
        return {"items": [], "query": query}
    hits: list[Hit] = []
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        for p in PROVIDERS:
            try:
                await protected_action(conn, caller, action=p.action, resource=p.resource)
            except ZidError:
                continue  # not readable by this caller: contributes nothing, and says nothing
            hits.extend(rank(query, await p.rows(conn), type=p.type, href=p.href))
    groups = {p.type: p.label for p in PROVIDERS}
    return {
        "query": query[:MAX_QUERY],
        "items": [
            {
                "type": h.type,
                "group": groups[h.type],
                "id": h.id,
                "label": h.label,
                "hint": h.hint,
                "href": h.href,
            }
            for h in hits
        ],
    }
