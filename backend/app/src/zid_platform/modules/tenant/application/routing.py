"""Placement and lifecycle enforcement on every authenticated request (ZID-TENANCY-001 §9 steps 4-5;
TEN-10; OVERLAY-001 §2).

A cell serves a tenant only if it is the tenant's authoritative home cell: the residency placement
decision when there is one, otherwise the cell that holds the tenant record. Tenants with neither
are served by the cell that holds them. The tenant's lifecycle status rides along, so SUSPENDED and
CLOSED tenants are refused decisions. The lookup is cached for a short TTL in-process and dropped
when this process changes the tenant's residency or lifecycle, so a migration cutover or suspension
takes effect within the TTL everywhere and at once locally.
"""

from __future__ import annotations

import time
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.tenant.infrastructure import residency_store as store

TTL_S = 30.0
# tenant -> (at, home cell, lifecycle status, placement epoch)
_cache: dict[str, tuple[float, str | None, str | None, int]] = {}


def forget(tenant_id: str) -> None:
    _cache.pop(tenant_id, None)


async def tenant_state(conn: AsyncConnection, tenant_id: str) -> tuple[str | None, str | None]:
    """(home cell, lifecycle status); None where the tenant has no such record here."""
    hit = _cache.get(tenant_id)
    now = time.monotonic()
    if hit is not None and now - hit[0] < TTL_S:
        return hit[1], hit[2]
    home, status, _ = await _load(conn, tenant_id, now)
    return home, status


async def _load(
    conn: AsyncConnection, tenant_id: str, now: float
) -> tuple[str | None, str | None, int]:
    r = await store.load(conn, tenant_id)
    row = (
        await conn.execute(
            text("SELECT home_cell_id, status FROM platform.tenant WHERE tenant_id = :t"),
            {"t": uuid.UUID(tenant_id)},
        )
    ).first()
    home = r.home_cell_id if r is not None else (row.home_cell_id if row else None)
    status = row.status if row else None
    epoch = r.placement_epoch if r is not None else 1
    _cache[tenant_id] = (now, home, status, epoch)
    return home, status, epoch


async def placement(conn: AsyncConnection, tenant_id: str) -> tuple[str | None, str | None, int]:
    """(home cell, lifecycle status, placement epoch) - cached like `tenant_state`."""
    hit = _cache.get(tenant_id)
    now = time.monotonic()
    if hit is not None and now - hit[0] < TTL_S:
        return hit[1], hit[2], hit[3]
    return await _load(conn, tenant_id, now)


async def home_cell(conn: AsyncConnection, tenant_id: str) -> str | None:
    return (await tenant_state(conn, tenant_id))[0]


def cached_tenant_state(tenant_id: str) -> tuple[bool, str | None, str | None]:
    known, home, status, _ = cached_placement(tenant_id)
    return known, home, status


def cached_placement(tenant_id: str) -> tuple[bool, str | None, str | None, int]:
    hit = _cache.get(tenant_id)
    if hit is not None and time.monotonic() - hit[0] < TTL_S:
        return True, hit[1], hit[2], hit[3]
    return False, None, None, 1


def cached_home_cell(tenant_id: str) -> tuple[bool, str | None]:
    known, home, _ = cached_tenant_state(tenant_id)
    return known, home


__all__ = [
    "cached_home_cell",
    "cached_placement",
    "cached_tenant_state",
    "forget",
    "home_cell",
    "placement",
    "tenant_state",
]
