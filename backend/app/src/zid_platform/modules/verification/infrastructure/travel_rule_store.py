"""Recorded Travel Rule messages (DTV-33; CR-101). Append-only, and only what was found: never the payload."""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

COLUMNS = (
    "message_id, direction, jurisdiction, payload_hash, originating_lei, beneficiary_lei, well_formed, finding_codes, "
    "originating_vasp, beneficiary_vasp, threshold, registry_version, recorded_by, created_at"
)


async def insert(
    conn: AsyncConnection, *, tenant_id: str, row: dict[str, Any]
) -> tuple[dict[str, Any], bool]:
    """The row and whether it is new. The same message in the same direction is recorded once."""
    got = (
        await conn.execute(
            text(
                "INSERT INTO verify.travel_rule_message (message_id, tenant_id, direction, jurisdiction, payload_hash, originating_lei, "
                "beneficiary_lei, well_formed, finding_codes, originating_vasp, beneficiary_vasp, threshold, registry_version, recorded_by) "
                "VALUES (:id, :t, :dir, :j, :h, :ol, :bl, :wf, CAST(:fc AS jsonb), :ov, :bv, :th, :rv, :by) "
                "ON CONFLICT (tenant_id, direction, payload_hash) DO NOTHING RETURNING " + COLUMNS
            ),
            {
                "id": uuid.uuid4(),
                "t": uuid.UUID(tenant_id),
                "dir": row["direction"],
                "j": row["jurisdiction"],
                "h": row["payload_hash"],
                "ol": row["originating_lei"],
                "bl": row["beneficiary_lei"],
                "wf": row["well_formed"],
                "fc": json.dumps(row["finding_codes"]),
                "ov": row["originating_vasp"],
                "bv": row["beneficiary_vasp"],
                "th": row["threshold"],
                "rv": row["registry_version"],
                "by": uuid.UUID(row["recorded_by"]),
            },
        )
    ).first()
    if got is not None:
        return dict(got._mapping), True
    existing = await find(conn, row["direction"], row["payload_hash"])
    assert existing is not None
    return existing, False


async def find(conn: AsyncConnection, direction: str, payload_hash: str) -> dict[str, Any] | None:
    r = (
        await conn.execute(
            text(
                f"SELECT {COLUMNS} FROM verify.travel_rule_message WHERE direction = :d AND payload_hash = :h"
            ),
            {"d": direction, "h": payload_hash},
        )
    ).first()
    return dict(r._mapping) if r else None


async def get(conn: AsyncConnection, message_id: str) -> dict[str, Any] | None:
    r = (
        await conn.execute(
            text(f"SELECT {COLUMNS} FROM verify.travel_rule_message WHERE message_id = :i"),
            {"i": uuid.UUID(message_id)},
        )
    ).first()
    return dict(r._mapping) if r else None


async def recent(conn: AsyncConnection, limit: int = 100) -> list[dict[str, Any]]:
    rows = await conn.execute(
        text(f"SELECT {COLUMNS} FROM verify.travel_rule_message ORDER BY created_at DESC LIMIT :n"),
        {"n": limit},
    )
    return [dict(r._mapping) for r in rows]
