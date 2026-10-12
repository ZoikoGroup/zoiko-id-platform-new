"""Mandate use cases that depend on authority (ZID-MANDATE-001 §10; MAND-06).

A mandate activates (or resumes from PAUSED) only if its source AuthorityGrant still resolves to a
valid chain at that moment; the source is read through the authority module's port.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.authority.ports import resolve
from zid_platform.modules.mandate.domain.ledger import LedgerError, MandateStatus
from zid_platform.modules.mandate.infrastructure.mandate_store import load_terms, set_mandate_status


async def activate_mandate(
    conn: AsyncConnection, *, tenant_id: str, mandate_id: str, risk_level: int, now: datetime
) -> MandateStatus:
    _, _, grant_id = await load_terms(conn, mandate_id)
    res = await resolve(
        conn, tenant_id=tenant_id, grant_id=grant_id, risk_level=risk_level, now=now
    )
    if not res.valid:
        raise LedgerError("MANDATE_SOURCE_INVALID", ", ".join(res.codes))
    return await set_mandate_status(conn, mandate_id, MandateStatus.ACTIVE, source_valid=True)


__all__ = ["MandateStatus", "activate_mandate"]
