"""Signals worth attention about the company's agents (ZID-CP-WF07 v2.0 A07-14; CR-169). Read only; see `domain.risk_signals`."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.machine.application.agents import sponsor_is_active
from zid_platform.modules.machine.application.idp import STALE_AFTER
from zid_platform.modules.machine.domain import risk_signals as rs
from zid_platform.modules.machine.infrastructure import (
    action_store,
    agent_store,
    idp_store,
    revocation_store,
)

LIMIT = 200


async def signals(conn: AsyncConnection, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    found: list[dict[str, Any]] = []
    found += rs.unresolved(await action_store.unresolved_actions(conn, limit=LIMIT), now)
    found += rs.providers(
        await idp_store.active_bindings(conn, limit=LIMIT), now, timedelta(seconds=STALE_AFTER)
    )
    found += rs.open_stops(await revocation_store.list_events(conn, open_only=True, limit=LIMIT))
    orphans: list[str] = []
    for a in await agent_store.list_agents(conn, status="ACTIVE", limit=LIMIT):
        sponsor = a.get("sponsor_principal_id")
        if sponsor and not await sponsor_is_active(conn, str(sponsor)):
            orphans.append(str(a["agent_principal_id"]))
    found += rs.orphaned(orphans)
    return rs.report(found, now)
