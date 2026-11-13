"""Changing a provider entitlement's status (ZID-INT-002 section 08). One implementation for the provider routes and for callers in other modules.

Revoked is final (a trigger refuses to reopen it). Only a revocation is announced as `provider.entitlement_revoked`, with ids, the provider, the service
and the previous status: never free text. A suspension disables the path without being announced as a revocation.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection
from zid_outbox import emit

from zid_platform.modules.verification.infrastructure import route_entitlement_store as store


class EntitlementNotFound(Exception):
    """There is no live (not revoked) entitlement for this provider and service."""


async def change_status(
    conn: AsyncConnection, tenant_id: str, provider_id: str, service: str, status: str, reason: str
) -> dict[str, Any]:
    if status not in ("SUSPENDED", "REVOKED"):
        raise ValueError("status must be SUSPENDED or REVOKED")
    current = await store.live(conn, provider_id, service)
    if current is None:
        raise EntitlementNotFound(f"{provider_id}/{service}")
    ident = str(current["entitlement_id"])
    await store.set_status(conn, ident, status, reason)
    if status == "REVOKED":
        await emit(
            conn,
            name="provider.entitlement_revoked",
            tenant_id=tenant_id,
            aggregate_type="provider_entitlement",
            aggregate_id=ident,
            aggregate_version=1,
            data={
                "entitlement_id": ident,
                "provider_id": provider_id,
                "service_code": service,
                "previous_status": current["status"],
            },
        )
    rows = [r for r in await store.listing(conn, provider_id) if str(r["entitlement_id"]) == ident]
    return rows[0]
