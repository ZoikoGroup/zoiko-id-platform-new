"""Family wording revisions (`commercial.service_family_text`, migration commercial_0037). The rules are repeated by the database."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.commercial.domain import family_text as rules
from zid_platform.modules.commercial.domain.catalogue import CommercialError
from zid_platform.modules.commercial.infrastructure.family_registry import family_registry

_COLS = "rev_id, family, version, name, unit, sales_qualifier, reason, status, created_by, created_at, activated_by, activated_at"


async def revisions(conn: AsyncConnection, family: str | None = None) -> list[dict[str, Any]]:
    rows = (
        (
            await conn.execute(
                text(
                    f"SELECT {_COLS} FROM commercial.service_family_text "
                    "WHERE (CAST(:f AS text) IS NULL OR family = CAST(:f AS text)) ORDER BY family, version DESC"
                ),
                {"f": family},
            )
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


async def active_texts(conn: AsyncConnection) -> dict[str, rules.Text]:
    rows = (
        await conn.execute(
            text(
                "SELECT family, name, unit, sales_qualifier FROM commercial.service_family_text WHERE status = 'ACTIVE'"
            )
        )
    ).all()
    return {r[0]: rules.Text(r[1], r[2], r[3]) for r in rows}


async def create_draft(
    conn: AsyncConnection,
    *,
    family: str,
    name: str,
    unit: str,
    sales_qualifier: str,
    reason: str,
    created_by: str,
) -> dict[str, Any]:
    if family_registry().get(family) is None:
        raise CommercialError("FAMILY_UNKNOWN", family)
    try:
        t = rules.check_text(name, unit, sales_qualifier)
    except rules.TextError as e:
        raise CommercialError(e.code, e.detail) from e
    version = int(
        (
            await conn.execute(
                text(
                    "SELECT coalesce(max(version), 0) + 1 FROM commercial.service_family_text WHERE family = :f"
                ),
                {"f": family},
            )
        ).scalar_one()
    )
    rev = uuid.uuid4()
    try:
        async with conn.begin_nested():
            await conn.execute(
                text(
                    "INSERT INTO commercial.service_family_text (rev_id, family, version, name, unit, sales_qualifier, reason, created_by) "
                    "VALUES (:i, :f, :v, :n, :u, :q, :r, :c)"
                ),
                {"i": rev, "f": family, "v": version, "n": t.name, "u": t.unit, "q": t.sales_qualifier, "r": reason.strip(), "c": uuid.UUID(created_by)},
            )  # fmt: skip
    except DBAPIError as e:
        raise CommercialError("FAMILY_TEXT_REFUSED", str(e.orig)[:160]) from e
    return {"rev_id": str(rev), "family": family, "version": version, "status": "DRAFT"}


async def _get(conn: AsyncConnection, rev_id: str) -> dict[str, Any] | None:
    r = (
        (
            await conn.execute(
                text(f"SELECT {_COLS} FROM commercial.service_family_text WHERE rev_id = :i"),
                {"i": uuid.UUID(rev_id)},
            )
        )
        .mappings()
        .first()
    )
    return dict(r) if r else None


async def activate(conn: AsyncConnection, rev_id: str, *, activator: str, now: datetime) -> None:
    """DRAFT to ACTIVE by someone other than the author; that family's previous active wording is superseded."""
    r = await _get(conn, rev_id)
    if r is None:
        raise CommercialError("FAMILY_TEXT_NOT_FOUND")
    if r["status"] != "DRAFT":
        raise CommercialError("FAMILY_TEXT_NOT_DRAFT")
    if str(r["created_by"]) == activator:
        raise CommercialError(
            "FAMILY_TEXT_SELF_ACTIVATION", "the author cannot activate their own wording"
        )
    try:
        async with conn.begin_nested():
            await conn.execute(
                text(
                    "UPDATE commercial.service_family_text SET status = 'SUPERSEDED' WHERE family = :f AND status = 'ACTIVE'"
                ),
                {"f": r["family"]},
            )
            await conn.execute(
                text(
                    "UPDATE commercial.service_family_text SET status = 'ACTIVE', activated_by = :a, activated_at = :n WHERE rev_id = :i"
                ),
                {"a": uuid.UUID(activator), "n": now, "i": uuid.UUID(rev_id)},
            )
    except DBAPIError as e:
        raise CommercialError("FAMILY_TEXT_REFUSED", str(e.orig)[:160]) from e


async def withdraw(conn: AsyncConnection, rev_id: str) -> None:
    r = await _get(conn, rev_id)
    if r is None:
        raise CommercialError("FAMILY_TEXT_NOT_FOUND")
    if r["status"] != "DRAFT":
        raise CommercialError("FAMILY_TEXT_NOT_DRAFT")
    await conn.execute(
        text("UPDATE commercial.service_family_text SET status = 'WITHDRAWN' WHERE rev_id = :i"),
        {"i": uuid.UUID(rev_id)},
    )
