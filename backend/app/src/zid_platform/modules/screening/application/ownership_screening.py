"""Read each owner's latest screening case and roll a company's owners up (ZID-DTV-WF-06 section 12; CR-120).

The ownership analysis (WF02) belongs to the organization module and is passed in as plain owners (a person reference and the analysis's status word); this module never imports it. A person's screen is the
latest screening case whose subject reference is that person, in the caller's tenant (row-level security).
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.screening.domain import ownership_rollup as rr


async def screens_of(conn: AsyncConnection, person_refs: Iterable[str]) -> list[rr.OwnerScreen]:
    refs = sorted(set(person_refs))
    if not refs:
        return []
    rows = (
        await conn.execute(
            text(
                "SELECT DISTINCT ON (subject_ref) subject_ref, state, screened_at FROM verify.screening_request "
                "WHERE subject_ref = ANY(:refs) ORDER BY subject_ref, created_at DESC"
            ),
            {"refs": refs},
        )
    ).all()
    return [rr.OwnerScreen(r.subject_ref, r.state, r.screened_at) for r in rows]


async def company_rollup(
    conn: AsyncConnection, owners: Iterable[rr.Owner], *, now: datetime, max_age: timedelta
) -> rr.Result:
    owner_list = list(owners)
    return rr.rollup(
        owner_list,
        await screens_of(conn, (o.person_ref for o in owner_list)),
        now=now,
        max_age=max_age,
    )


async def owners_needing_a_case(
    conn: AsyncConnection, owners: Iterable[rr.Owner], *, now: datetime, max_age: timedelta
) -> tuple[str, ...]:
    owner_list = list(owners)
    return rr.needs_screening(
        owner_list,
        await screens_of(conn, (o.person_ref for o in owner_list)),
        now=now,
        max_age=max_age,
    )
