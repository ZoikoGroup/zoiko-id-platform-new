"""Retention of the partner request log (ZID-CP-WF10 v2.0, WF-10.10; CR-173).

One call removes request-log rows older than the retention period, through the only function that may (it refuses a period under 90 days and never touches a newer row). It is meant to be
run on a schedule by the cell's maintenance worker; nothing here schedules it. The period is a default the owners must confirm.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

DEFAULT_DAYS = 90
MIN_DAYS = 90


async def purge_request_log(conn: AsyncConnection, days: int = DEFAULT_DAYS) -> int:
    """How many rows were removed. Raises when the period is under the minimum."""
    if days < MIN_DAYS:
        raise ValueError(f"the request log is kept for at least {MIN_DAYS} days")
    return int((await conn.execute(text("SELECT verify.api_purge_request_log(:d)"), {"d": days})).scalar_one())
