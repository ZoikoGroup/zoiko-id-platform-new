"""Recorded ownership graph versions (DTV-24; CR-102). Append-only; row-level security scopes every read to the caller's company."""

from __future__ import annotations

import json
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

COLUMNS = "graph_id, target, version, entities, links, threshold, content_hash, author, created_at"


async def next_version(conn: AsyncConnection, target: str) -> int:
    got = (
        await conn.execute(
            text("SELECT coalesce(max(version), 0) + 1 FROM org.ownership_graph WHERE target = :t"),
            {"t": target},
        )
    ).scalar_one()
    return int(got)


async def insert(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    target: str,
    version: int,
    entities: list[dict[str, Any]],
    links: list[dict[str, Any]],
    threshold: Decimal,
    content_hash: str,
    author: str,
) -> dict[str, Any]:
    row = (
        await conn.execute(
            text(
                "INSERT INTO org.ownership_graph (graph_id, tenant_id, target, version, entities, links, threshold, content_hash, author) "
                "VALUES (:id, :t, :target, :v, CAST(:e AS jsonb), CAST(:l AS jsonb), :th, :h, :a) RETURNING "
                + COLUMNS
            ),
            {
                "id": uuid.uuid4(),
                "t": uuid.UUID(tenant_id),
                "target": target,
                "v": version,
                "e": json.dumps(entities),
                "l": json.dumps(links),
                "th": threshold,
                "h": content_hash,
                "a": uuid.UUID(author),
            },
        )
    ).first()
    assert row is not None
    return dict(row._mapping)


async def get(conn: AsyncConnection, graph_id: str) -> dict[str, Any] | None:
    row = (
        await conn.execute(
            text(f"SELECT {COLUMNS} FROM org.ownership_graph WHERE graph_id = :i"),
            {"i": uuid.UUID(graph_id)},
        )
    ).first()
    return dict(row._mapping) if row else None


async def recent(conn: AsyncConnection, limit: int = 100) -> list[dict[str, Any]]:
    rows = await conn.execute(
        text(f"SELECT {COLUMNS} FROM org.ownership_graph ORDER BY created_at DESC LIMIT :n"),
        {"n": limit},
    )
    return [dict(r._mapping) for r in rows]


CONF_COLUMNS = (
    "confirmation_id, graph_id, entity_id, decision, reason, evidence_ref, reviewer, created_at"
)


async def insert_confirmation(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    graph_id: str,
    entity_id: str,
    decision: str,
    reason: str,
    evidence_ref: str,
    reviewer: str,
) -> dict[str, Any]:
    row = (
        await conn.execute(
            text(
                "INSERT INTO org.ownership_confirmation (confirmation_id, tenant_id, graph_id, entity_id, decision, reason, evidence_ref, reviewer) "
                "VALUES (:id, :t, :g, :e, :d, :r, :ev, :rv) RETURNING " + CONF_COLUMNS
            ),
            {
                "id": uuid.uuid4(),
                "t": uuid.UUID(tenant_id),
                "g": uuid.UUID(graph_id),
                "e": entity_id,
                "d": decision,
                "r": reason,
                "ev": evidence_ref,
                "rv": uuid.UUID(reviewer),
            },
        )
    ).first()
    assert row is not None
    return dict(row._mapping)


async def confirmations_for(conn: AsyncConnection, graph_id: str) -> list[dict[str, Any]]:
    rows = await conn.execute(
        text(
            f"SELECT {CONF_COLUMNS} FROM org.ownership_confirmation WHERE graph_id = :g ORDER BY created_at"
        ),
        {"g": uuid.UUID(graph_id)},
    )
    return [dict(r._mapping) for r in rows]
