"""Public Protocol interface of the credential module. Other modules import ONLY this."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection


async def credentials_issued_between(
    conn: AsyncConnection, start: datetime, end: datetime
) -> list[tuple[str, datetime]]:
    """(credential id, issued at) of this tenant's credentials in [start, end), for billing reconciliation."""
    rows = (
        await conn.execute(
            text(
                "SELECT credential_id, issued_at FROM cred.credential_record "
                "WHERE issued_at >= :s AND issued_at < :e ORDER BY issued_at"
            ),
            {"s": start, "e": end},
        )
    ).all()
    return [(str(r[0]), r[1]) for r in rows]
