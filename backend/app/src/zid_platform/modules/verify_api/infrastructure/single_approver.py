"""Tell the database, for this transaction only, that the deployment has chosen single-approver mode (ZID_VERIFY_SINGLE_APPROVER).

The go-live request, a price and a production-credential approval each have a table rule that the person who decides is not the person who asked (migration verify_api_0019). The rule yields only when the
transaction says `app.verify_single_approver = 'on'`. This sets it, and only when the application's own setting is on: with the setting off nothing is set and the table refuses as it always did.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection


async def apply(conn: AsyncConnection, on: bool) -> None:
    if on:
        await conn.execute(text("SELECT set_config('app.verify_single_approver', 'on', true)"))
