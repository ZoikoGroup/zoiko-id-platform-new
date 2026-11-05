"""Reporting by organization unit: who is in a unit (and the units beneath it).

Decision must not import tenant (a dependency cycle), so the lookup is handed in at start-up (`main.py`). It answers None when there is no such live
unit, which the caller reports as not found rather than as an empty report."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncConnection

UnitLookup = Callable[[AsyncConnection, str], Awaitable[list[str] | None]]
_lookup: UnitLookup | None = None


def register_unit_lookup(lookup: UnitLookup | None) -> None:
    global _lookup
    _lookup = lookup


async def unit_principals(conn: AsyncConnection, unit_id: str) -> list[str] | None:
    return await _lookup(conn, unit_id) if _lookup is not None else None
