"""Public Protocol interface of the integration module. Other modules import ONLY this.

- `record_interaction(conn, source_ref, ok=...)`: the federation / provisioning modules report each
  interaction of a source they run (`federation_connection:<id>`, `provisioning_source:<id>`), so
  the connector framework tracks health and freshness (OVERLAY-001 §15, §24).
- `connector_fresh(conn, source_ref, now)`: whether a connector-supplied fact may still back a
  high-risk decision (False when stale or quarantined; True when no connector governs the source).
- `map_record(conn, connector_id, record)`: the connector ACTIVE mapping release applied to one
  source record (profile facts, relation facts and role candidates only; never authority).
- `route_login(conn, application_id=..., login_domain=...)`: the deterministic multi-IdP route.
- `pep_application(conn, client_id)`: the registered application whose PEP this client is.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.integration.application.mapping import map_record, route_login
from zid_platform.modules.integration.application.service import (
    offboard_integrations,
    record_interaction,
)
from zid_platform.modules.integration.domain.model import Health, fresh
from zid_platform.modules.integration.infrastructure.store import (
    Application,
    active_application_count,
)
from zid_platform.modules.integration.infrastructure.store import (
    application_for_pep as pep_application,
)


async def connector_fresh(conn: AsyncConnection, source_ref: str, now: datetime) -> bool:
    from zid_platform.modules.integration.infrastructure.store import connector_for_source

    c = await connector_for_source(conn, source_ref)
    if c is None:
        return True
    return c.health is not Health.QUARANTINED and fresh(c.fresh_until, now)


__all__ = [
    "Application",
    "active_application_count",
    "connector_fresh",
    "map_record",
    "offboard_integrations",
    "pep_application",
    "record_interaction",
    "route_login",
]
