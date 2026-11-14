"""Versioned public notices (`commercial.public_notice`, migration commercial_0039). Draft, activated by a different person, then immutable; the next version supersedes it."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.commercial.domain.catalogue import CommercialError

KIND = "PRICING_INQUIRY"
_COLS = "notice_id, kind, version, body, status, created_by, created_at, activated_by, activated_at"


async def notices(conn: AsyncConnection) -> list[dict[str, Any]]:
    rows = (
        (
            await conn.execute(
                text(
                    f"SELECT {_COLS} FROM commercial.public_notice WHERE kind = :k ORDER BY created_at DESC"
                ),
                {"k": KIND},
            )
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


async def propose(
    conn: AsyncConnection, *, version: str, body: str, created_by: str
) -> dict[str, Any]:
    nid = uuid.uuid4()
    try:
        async with conn.begin_nested():
            await conn.execute(
                text(
                    "INSERT INTO commercial.public_notice (notice_id, kind, version, body, created_by) VALUES (:i, :k, :v, :b, :c)"
                ),
                {
                    "i": nid,
                    "k": KIND,
                    "v": version.strip(),
                    "b": body.strip(),
                    "c": uuid.UUID(created_by),
                },
            )
    except IntegrityError as e:
        # a repeated version is a unique violation; anything else (a version or body outside the allowed shape) is a refusal
        if "duplicate key" in str(e.orig).lower():
            raise CommercialError("NOTICE_VERSION_EXISTS", version) from e
        raise CommercialError("NOTICE_REFUSED", str(e.orig)[:160]) from e
    except DBAPIError as e:
        raise CommercialError("NOTICE_REFUSED", str(e.orig)[:160]) from e
    return {"notice_id": str(nid), "version": version.strip(), "status": "DRAFT"}


async def _get(conn: AsyncConnection, notice_id: str) -> dict[str, Any] | None:
    r = (
        (
            await conn.execute(
                text(f"SELECT {_COLS} FROM commercial.public_notice WHERE notice_id = :i"),
                {"i": uuid.UUID(notice_id)},
            )
        )
        .mappings()
        .first()
    )
    return dict(r) if r else None


async def activate(conn: AsyncConnection, notice_id: str, *, activator: str, now: datetime) -> None:
    r = await _get(conn, notice_id)
    if r is None:
        raise CommercialError("NOTICE_NOT_FOUND")
    if r["status"] != "DRAFT":
        raise CommercialError("NOTICE_NOT_DRAFT")
    if str(r["created_by"]) == activator:
        raise CommercialError(
            "NOTICE_SELF_ACTIVATION", "the author cannot activate their own notice"
        )
    try:
        async with conn.begin_nested():
            await conn.execute(
                text(
                    "UPDATE commercial.public_notice SET status = 'SUPERSEDED' WHERE kind = :k AND status = 'ACTIVE'"
                ),
                {"k": KIND},
            )
            await conn.execute(
                text(
                    "UPDATE commercial.public_notice SET status = 'ACTIVE', activated_by = :a, activated_at = :n WHERE notice_id = :i"
                ),
                {"a": uuid.UUID(activator), "n": now, "i": uuid.UUID(notice_id)},
            )
    except DBAPIError as e:
        raise CommercialError("NOTICE_REFUSED", str(e.orig)[:160]) from e


async def withdraw(conn: AsyncConnection, notice_id: str) -> None:
    r = await _get(conn, notice_id)
    if r is None:
        raise CommercialError("NOTICE_NOT_FOUND")
    if r["status"] != "DRAFT":
        raise CommercialError("NOTICE_NOT_DRAFT")
    await conn.execute(
        text("UPDATE commercial.public_notice SET status = 'WITHDRAWN' WHERE notice_id = :i"),
        {"i": uuid.UUID(notice_id)},
    )
