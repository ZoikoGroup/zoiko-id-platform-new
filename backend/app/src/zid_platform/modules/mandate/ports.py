"""Public interface of the mandate module. Other modules import ONLY this.

DR (G-W7; RESILIENCE §17): `advance_writer(conn, tenant, new_cell)` moves the single C3 writer to a
new cell under a strictly higher epoch (requests carrying the old epoch are refused everywhere);
`quarantine_open_reservations` marks every RESERVED reservation RECONCILIATION_REQUIRED - DR never
frees authority to improve availability.
"""

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.mandate.application.decision_check import MandateCheck, check_for_decision
from zid_platform.modules.mandate.domain.subagent import CascadeEffect
from zid_platform.modules.mandate.infrastructure.mandate_store import (
    MandateMetadata,
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


__all__ = [
    "CascadeEffect",
    "MandateCheck",
    "MandateMetadata",
    "advance_writer",
    "check_for_decision",
    "mandate_metadata_page",
    "pause_or_revoke_for_grants",
    "quarantine_open_reservations",
]
