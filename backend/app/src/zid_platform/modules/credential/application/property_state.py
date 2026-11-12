"""Keeping the last property-check answer so a change in authority can be announced (ZID-INT-002 section 13, `property.authority_changed`).

Only outcomes are kept, per property and host. The first check is not a change; an identical answer is not a change; a different overall
outcome, or a different outcome in any dimension, is. The event names the references, the before and after of each dimension that moved,
and nothing from any document.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection
from zid_outbox import emit

from zid_platform.modules.credential.infrastructure import trust_store

NAMESPACE = uuid.UUID("5d0a8a52-3b0e-4f0e-9a43-0f3a7c1a9e11")


def _outcomes(out: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(d["dimension"]): {"outcome": d["outcome"], "reasons": list(d.get("reasons", []))}
        for d in out.get("dimensions", [])
    }


def changes(
    before: Mapping[str, Mapping[str, Any]], after: Mapping[str, Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """The dimensions whose outcome moved. A dimension that was not asked for this time is neither added nor removed."""
    moved = []
    for name in sorted(set(before) & set(after)):
        if before[name]["outcome"] != after[name]["outcome"]:
            moved.append(
                {"dimension": name, "from": before[name]["outcome"], "to": after[name]["outcome"]}
            )
    return moved


async def record_and_announce(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    property_ref: str,
    host_ref: str,
    result: Mapping[str, Any],
) -> bool:
    """Store this answer; emit `property.authority_changed` when it differs from the one before. True if an event was emitted."""
    overall = str(result["listing_authority"])
    now_dims = _outcomes(result)
    row = (
        await conn.execute(
            text(
                "SELECT overall, dimensions FROM cred.property_check_state "
                "WHERE property_ref = :p AND host_ref = :h FOR UPDATE"
            ),
            {"p": property_ref, "h": host_ref},
        )
    ).first()
    await conn.execute(
        text(
            "INSERT INTO cred.property_check_state (tenant_id, property_ref, host_ref, overall, dimensions) "
            "VALUES (CAST(:t AS uuid), :p, :h, :o, CAST(:d AS jsonb)) "
            "ON CONFLICT (tenant_id, property_ref, host_ref) DO UPDATE SET "
            "overall = EXCLUDED.overall, dimensions = EXCLUDED.dimensions, updated_at = now(), "
            "checks_made = cred.property_check_state.checks_made + 1"
        ),
        {
            "t": tenant_id,
            "p": property_ref,
            "h": host_ref,
            "o": overall,
            "d": json.dumps(now_dims),
        },
    )
    if row is None:
        return False
    previous_overall, previous_dims = str(row[0]), row[1]
    moved = changes(previous_dims, now_dims)
    if previous_overall == overall and not moved:
        return False
    aggregate = str(uuid.uuid5(NAMESPACE, f"{tenant_id}|{property_ref}|{host_ref}"))
    await emit(
        conn,
        name="property.authority_changed",
        tenant_id=tenant_id,
        aggregate_type="property_authority",
        aggregate_id=aggregate,
        aggregate_version=await trust_store.next_event_version(
            conn, "property_authority", aggregate
        ),
        data={
            "property_ref": property_ref,
            "host_ref": host_ref,
            "previous_listing_authority": previous_overall,
            "current_listing_authority": overall,
            "dimensions_changed": moved,
        },
    )
    return True
