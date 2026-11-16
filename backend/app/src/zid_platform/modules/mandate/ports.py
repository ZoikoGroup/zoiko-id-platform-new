"""Public interface of the mandate module. Other modules import ONLY this.

DR (G-W7; RESILIENCE §17): `advance_writer(conn, tenant, new_cell)` moves the single C3 writer to a
new cell under a strictly higher epoch (requests carrying the old epoch are refused everywhere);
`quarantine_open_reservations` marks every RESERVED reservation RECONCILIATION_REQUIRED - DR never
frees authority to improve availability.
"""

import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.mandate.application.decision_check import MandateCheck, check_for_decision
from zid_platform.modules.mandate.domain.subagent import CascadeEffect
from zid_platform.modules.mandate.infrastructure.mandate_store import (
    MandateMetadata,
    list_mandates,
    mandate_metadata_page,
    pause_or_revoke_for_grants,
)


async def advance_writer(conn: AsyncConnection, tenant_id: str, new_cell: uuid.UUID) -> int:
    row = (
        await conn.execute(
            text(
                "INSERT INTO agent.writer_fence (tenant_id, cell_id, epoch) VALUES (:t, :c, 1) "
                "ON CONFLICT (tenant_id) DO UPDATE SET cell_id = EXCLUDED.cell_id, "
                "epoch = agent.writer_fence.epoch + 1, updated_at = now() RETURNING epoch"
            ),
            {"t": uuid.UUID(tenant_id), "c": new_cell},
        )
    ).one()
    return int(row.epoch)


async def quarantine_open_reservations(conn: AsyncConnection) -> int:
    r = await conn.execute(
        text(
            "UPDATE agent.mandate_reservation SET reservation_status = 'RECONCILIATION_REQUIRED' "
            "WHERE reservation_status = 'RESERVED'"
        )
    )
    return int(r.rowcount or 0)


async def metadata_projection(conn: AsyncConnection, *, limit: int) -> list[dict[str, Any]]:
    """The minimal, read-only view of the tenant's mandates that an access-readiness review needs (ZID-AG-WF-001 section 06): the
    mandate's own id, state, end date and the actions it allows, plus what its source grant allows and whether that grant is still
    live. No represented person, no resource scope, no value, no purpose text: nothing a reviewer or a model could be misled by.
    The caller owns pagination: it asks for one more than it will keep so an overflow is never silent."""
    from zid_platform.modules.authority.ports import (
        load_grant,  # here, not at the top: authority imports this module
    )

    out: list[dict[str, Any]] = []
    for m in await list_mandates(conn, limit=limit):
        grant = await load_grant(conn, m.authority_grant_id)
        allowed = grant.authority.actions if grant is not None else None
        out.append(
            {
                "mandate_id": m.terms.mandate_id,
                "status": m.terms.status.value,
                "valid_to": m.terms.valid_to,
                "actions": sorted(m.terms.actions),
                "approved_actions": sorted(allowed) if isinstance(allowed, frozenset) else None,
                "grant_found": grant is not None,
                "grant_status": grant.status.value if grant is not None else None,
            }
        )
    return out


__all__ = [
    "CascadeEffect",
    "MandateCheck",
    "MandateMetadata",
    "advance_writer",
    "check_for_decision",
    "mandate_metadata_page",
    "metadata_projection",
    "pause_or_revoke_for_grants",
    "quarantine_open_reservations",
]
