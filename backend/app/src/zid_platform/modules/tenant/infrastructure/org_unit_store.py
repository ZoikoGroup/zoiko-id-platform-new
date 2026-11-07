from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection


async def unit_principals(conn: AsyncConnection, unit_id: str) -> list[str] | None:
    """The people in a unit and in every unit beneath it (current members only), or None when there is no live unit by that id.

    Reporting by unit uses this: a group's figures include its subsidiaries and departments, and a person who left no longer counts."""
    try:
        uid = uuid.UUID(unit_id)
    except ValueError:
        return None
    live = (
        await conn.execute(
            text("SELECT 1 FROM platform.org_unit WHERE unit_id = :u AND status = 'ACTIVE'"),
            {"u": uid},
        )
    ).first()
    if live is None:
        return None
    rows = (
        await conn.execute(
            text(
                "WITH RECURSIVE tree AS ("
                " SELECT unit_id FROM platform.org_unit WHERE unit_id = :u AND status = 'ACTIVE'"
                " UNION ALL"
                " SELECT c.unit_id FROM platform.org_unit c JOIN tree t ON c.parent_unit_id = t.unit_id WHERE c.status = 'ACTIVE'"
                ") SELECT DISTINCT m.principal_id FROM platform.org_unit_member m JOIN tree USING (unit_id) WHERE m.removed_at IS NULL"
            ),
            {"u": uid},
        )
    ).all()
    return [str(r.principal_id) for r in rows]
