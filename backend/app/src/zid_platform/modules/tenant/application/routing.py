"""Placement enforcement on every authenticated request (ZID-TENANCY-001 §9 steps 4-5; TEN-10).

A cell serves a tenant only if it is the tenant's authoritative home cell. Tenants without a
residency record (not yet placed) are served by the cell that holds them. The lookup is cached
for a short TTL in-process and dropped when this process changes the tenant's residency, so a
migration cutover takes effect within the TTL everywhere and at once locally.
"""

from __future__ import annotations

import time

from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.tenant.infrastructure import residency_store as store

TTL_S = 30.0
_cache: dict[str, tuple[float, str | None]] = {}


def forget(tenant_id: str) -> None:
    _cache.pop(tenant_id, None)


async def home_cell(conn: AsyncConnection, tenant_id: str) -> str | None:
    hit = _cache.get(tenant_id)
    now = time.monotonic()
    if hit is not None and now - hit[0] < TTL_S:
        return hit[1]
    r = await store.load(conn, tenant_id)
    value = r.home_cell_id if r is not None else None
    _cache[tenant_id] = (now, value)
    return value


def cached_home_cell(tenant_id: str) -> tuple[bool, str | None]:
    hit = _cache.get(tenant_id)
    if hit is not None and time.monotonic() - hit[0] < TTL_S:
        return True, hit[1]
    return False, None


__all__ = ["cached_home_cell", "forget", "home_cell"]
