"""Saved solution configurations (`commercial.solution_draft`, migration commercial_0030; ZID-COM-WF-2026-11 WF-C02).

A draft is immutable once saved. The one change is the move to QUOTE_REQUESTED, which freezes the snapshot the deal desk is given. Tenant rows are isolated by
row-level security; `operator_requests` reads them across tenants for the operator plane only.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.commercial.domain.catalogue import CommercialError
from zid_platform.modules.commercial.infrastructure.plan_request_store import operator_scope

_COLS = (
    "draft_id, tenant_id, created_by, purpose, families, subject_country, payer_country, operating_region, volumes, scope_hash, "
    "status, created_at, expires_at, quote_snapshot, quote_requested_by, quote_requested_at"
)


def _view(r: Any, now: datetime) -> dict[str, Any]:
    d = dict(r._mapping)
    d["draft_id"] = str(d["draft_id"])
    d["tenant_id"] = str(d["tenant_id"])
    d["created_by"] = str(d["created_by"])
    if d["quote_requested_by"] is not None:
        d["quote_requested_by"] = str(d["quote_requested_by"])
    d["families"] = list(d["families"])
    # a draft past its date reads as EXPIRED; it is never silently revived
    d["state"] = "EXPIRED" if d["status"] == "DRAFT" and d["expires_at"] < now else d["status"]
    return d


async def save(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    created_by: str,
    purpose: str,
    families: list[str],
    subject_country: str | None,
    payer_country: str | None,
    operating_region: str | None,
    volumes: dict[str, dict[str, int]],
    scope_hash: str,
) -> dict[str, Any]:
    draft_id = uuid.uuid4()
    await conn.execute(
        text(
            "INSERT INTO commercial.solution_draft (draft_id, tenant_id, created_by, purpose, families, subject_country, payer_country, "
            "operating_region, volumes, scope_hash) VALUES (:i, :t, :c, :p, :f, :s, :y, :o, CAST(:v AS jsonb), :h)"
        ),
        {
            "i": draft_id, "t": uuid.UUID(tenant_id), "c": uuid.UUID(created_by), "p": purpose,
            "f": sorted(set(families)), "s": subject_country, "y": payer_country, "o": operating_region,
            "v": json.dumps(volumes, sort_keys=True), "h": scope_hash,
        },
    )  # fmt: skip
    found = await get(conn, str(draft_id))
    assert found is not None
    return found


async def get(conn: AsyncConnection, draft_id: str) -> dict[str, Any] | None:
    r = (
        await conn.execute(
            text(f"SELECT {_COLS} FROM commercial.solution_draft WHERE draft_id = :i"),
            {"i": uuid.UUID(draft_id)},
        )
    ).first()
    return _view(r, datetime.now(UTC)) if r else None


async def mine(conn: AsyncConnection, limit: int = 50) -> list[dict[str, Any]]:
    rows = (
        await conn.execute(
            text(
                f"SELECT {_COLS} FROM commercial.solution_draft ORDER BY created_at DESC LIMIT :n"
            ),
            {"n": limit},
        )
    ).all()
    now = datetime.now(UTC)
    return [_view(r, now) for r in rows]


async def request_quote(
    conn: AsyncConnection, draft_id: str, *, requested_by: str, snapshot: dict[str, Any]
) -> dict[str, Any]:
    """DRAFT to QUOTE_REQUESTED, once, with the snapshot frozen. An expired draft, or one already sent, is refused."""
    d = await get(conn, draft_id)
    if d is None:
        raise CommercialError("SOLUTION_DRAFT_NOT_FOUND")
    if d["state"] == "EXPIRED":
        raise CommercialError("SOLUTION_DRAFT_EXPIRED", "save the configuration again")
    if d["status"] != "DRAFT":
        raise CommercialError("SOLUTION_QUOTE_ALREADY_REQUESTED")
    try:
        async with conn.begin_nested():
            await conn.execute(
                text(
                    "UPDATE commercial.solution_draft SET status = 'QUOTE_REQUESTED', quote_snapshot = CAST(:s AS jsonb), "
                    "quote_requested_by = :b, quote_requested_at = now() WHERE draft_id = :i AND status = 'DRAFT'"
                ),
                {
                    "s": json.dumps(snapshot, sort_keys=True),
                    "b": uuid.UUID(requested_by),
                    "i": uuid.UUID(draft_id),
                },
            )
    except DBAPIError as e:
        raise CommercialError("SOLUTION_QUOTE_REFUSED", str(e.orig)[:160]) from e
    done = await get(conn, draft_id)
    assert done is not None
    return done


async def operator_requests(conn: AsyncConnection, limit: int = 100) -> list[dict[str, Any]]:
    """Every company's quote requests, newest first, for the deal desk. Operator plane only."""
    async with operator_scope(conn):
        rows = (
            await conn.execute(
                text(
                    f"SELECT {_COLS} FROM commercial.solution_draft WHERE status = 'QUOTE_REQUESTED' "
                    "ORDER BY quote_requested_at DESC LIMIT :n"
                ),
                {"n": limit},
            )
        ).all()
    now = datetime.now(UTC)
    return [_view(r, now) for r in rows]
