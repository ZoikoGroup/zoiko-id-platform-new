"""Public Protocol interface of the organization module. Other modules import ONLY this.

The authority module reads roots through `RootReader` to validate the top of a grant chain; it never
touches org.* tables or the organization domain directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.organization.application.registry import (
    EstablishedRoot,
    NameMatcher,
    establish_development_root,
    establish_registry_root,
    refresh_registry_roots,
)
from zid_platform.modules.organization.domain.registry import RegistryError
from zid_platform.modules.organization.infrastructure.registry_adapters import (
    CompaniesHouse,
    Gleif,
    RegistryAdapter,
    adapter_for,
)

__all__ = [
    "CompaniesHouse",
    "EstablishedRoot",
    "Gleif",
    "NameMatcher",
    "RegistryAdapter",
    "RegistryError",
    "RootReader",
    "RootView",
    "adapter_for",
    "establish_development_root",
    "establish_registry_root",
    "has_usable_root",
    "read_root",
    "refresh_registry_roots",
]


@dataclass(frozen=True, slots=True)
class RootView:
    """What other modules may know about an authority root (ZID-ROOT-001 §13, minimized)."""

    root_id: str
    tenant_id: str
    organization_id: str
    holder_principal_id: str
    assurance: int  # 0..3 = AR-0..AR-3
    usable: bool  # ACTIVE (STALE/CONFLICTED/SUSPENDED/REVOKED/EXPIRED/SUPERSEDED are not)
    stale: bool  # STALE or CONFLICTED: current validity can't be established
    valid_from: datetime
    valid_to: datetime | None
    fresh_until: datetime
    scope_actions: frozenset[str] | None
    value_limit: Decimal | None
    currency: str | None


class RootReader(Protocol):
    async def __call__(
        self, conn: AsyncConnection, tenant_id: str, root_id: str
    ) -> RootView | None: ...


async def read_root(conn: AsyncConnection, tenant_id: str, root_id: str) -> RootView | None:
    """Default RootReader backed by the organization store."""
    from zid_platform.modules.organization.domain.roots import RootStatus
    from zid_platform.modules.organization.infrastructure.root_store import load_root

    r = await load_root(conn, root_id)
    if r is None:
        return None
    return RootView(
        root_id=r.root_id,
        tenant_id=tenant_id,
        organization_id=r.organization_id,
        holder_principal_id=r.holder_principal_id,
        assurance=int(r.assurance),
        usable=r.status is RootStatus.ACTIVE,
        stale=r.status in (RootStatus.STALE, RootStatus.CONFLICTED),
        valid_from=r.valid_from,
        valid_to=r.valid_to,
        fresh_until=r.fresh_until,
        scope_actions=r.scope_actions,
        value_limit=r.value_limit,
        currency=r.currency,
    )


async def has_usable_root(
    conn: AsyncConnection, tenant_id: str, organization_id: str, now: datetime
) -> bool:
    """Does the organization hold an ACTIVE, still-fresh authority root (onboarding gate)?"""
    from zid_platform.modules.organization.infrastructure.root_store import load_roots

    for r in await load_roots(conn, organization_id):
        view = await read_root(conn, tenant_id, r.root_id)
        if view is not None and view.usable and view.fresh_until > now:
            return True
    return False
