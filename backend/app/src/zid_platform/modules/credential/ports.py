"""Public Protocol interface of the credential module. Other modules import ONLY this."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.credential.application.claim_checks import configure_usage_sink
from zid_platform.modules.credential.application.fairness import check_disparity
from zid_platform.modules.credential.application.issuer_binding import resolve_and_bind
from zid_platform.modules.credential.application.trust_service import (
    recheck_due,
    review_proofs_of_lost_issuers,
)

__all__ = [
    "check_disparity",
    "configure_usage_sink",
    "credentials_issued_between",
    "recheck_due",
    "resolve_and_bind",
    "review_proofs_of_lost_issuers",
]


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
