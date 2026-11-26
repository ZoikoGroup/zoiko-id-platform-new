"""SQL for stops by scope (CR-169; machine_0014). Tenant transactions with RLS on; nothing here decides anything."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

_EVENT = "event_id, epoch, scope, subject_ref, reason, actor, targets, enforcement, freeze_case_id, effective_at, closed_by, closed_at, close_note"


def _row(x: Any) -> dict[str, Any]:
    return dict(x._mapping)


async def active_agents_of_sponsor(conn: AsyncConnection, sponsor_id: str) -> list[str]:
    rows = await conn.execute(
        text("SELECT agent_principal_id::text AS a FROM agent.agent_profile WHERE sponsor_principal_id = :s AND status = 'ACTIVE' ORDER BY created_at, agent_principal_id"), {"s": uuid.UUID(sponsor_id)}
    )
    return [x.a for x in rows]


async def active_agents_of_blueprint(conn: AsyncConnection, blueprint_id: str, version: str | None) -> list[str]:
    rows = await conn.execute(
        text(
            "SELECT agent_principal_id::text AS a FROM agent.agent_profile WHERE blueprint_id = :b AND (CAST(:v AS text) IS NULL OR blueprint_version = :v) AND status = 'ACTIVE' ORDER BY created_at, agent_principal_id"
        ),
        {"b": blueprint_id, "v": version},
    )
    return [x.a for x in rows]


async def active_agents_of_issuer(conn: AsyncConnection, issuer: str, key_id: str | None) -> list[str]:
    """ACTIVE agents with a live binding to this outside issuer (and, when a key is named, whose binding pins that key)."""
    rows = await conn.execute(
        text(
            "SELECT DISTINCT p.agent_principal_id::text AS a FROM agent.agent_profile p JOIN agent.idp_binding b ON b.agent_principal_id = p.agent_principal_id "
            "WHERE b.issuer = :i AND b.status = 'ACTIVE' AND p.status = 'ACTIVE' AND (CAST(:k AS text) IS NULL OR b.keys @> CAST(:kj AS jsonb)) ORDER BY 1"
        ),
        {"i": issuer, "k": key_id, "kj": json.dumps([{"kid": key_id}]) if key_id else None},
    )
    return [x.a for x in rows]


async def bindings_of_issuer(conn: AsyncConnection, issuer: str, key_id: str | None) -> list[str]:
    rows = await conn.execute(
        text("SELECT binding_id::text AS b FROM agent.idp_binding WHERE issuer = :i AND status = 'ACTIVE' AND (CAST(:k AS text) IS NULL OR keys @> CAST(:kj AS jsonb))"),
        {"i": issuer, "k": key_id, "kj": json.dumps([{"kid": key_id}]) if key_id else None},
    )
    return [x.b for x in rows]


async def active_agents(conn: AsyncConnection) -> list[str]:
    rows = await conn.execute(text("SELECT agent_principal_id::text AS a FROM agent.agent_profile WHERE status = 'ACTIVE' ORDER BY created_at, agent_principal_id"))
    return [x.a for x in rows]


async def next_epoch(conn: AsyncConnection, tenant_id: str) -> int:
    """The company's next stop number. The lock makes two stops at once take two numbers."""
    await conn.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"revocation:{tenant_id}"})
    return int((await conn.execute(text("SELECT COALESCE(max(epoch), 0) + 1 FROM agent.revocation_event"))).scalar_one())


async def insert_event(
    conn: AsyncConnection, tenant_id: str, *, event_id: str, epoch: int, scope: str, subject_ref: str, reason: str, actor: str, targets: dict[str, Any], enforcement: dict[str, Any],
    freeze_case_id: str | None, now: datetime,
) -> None:  # fmt: skip
    await conn.execute(
        text(
            "INSERT INTO agent.revocation_event (event_id, tenant_id, epoch, scope, subject_ref, reason, actor, targets, enforcement, freeze_case_id, effective_at) "
            "VALUES (:e, :t, :n, :s, :r, :why, :a, CAST(:tg AS jsonb), CAST(:en AS jsonb), :f, :at)"
        ),
        {
            "e": uuid.UUID(event_id), "t": uuid.UUID(tenant_id), "n": epoch, "s": scope, "r": subject_ref, "why": reason, "a": uuid.UUID(actor), "tg": json.dumps(targets), "en": json.dumps(enforcement),
            "f": uuid.UUID(freeze_case_id) if freeze_case_id else None, "at": now,
        },
    )  # fmt: skip


async def get_event(conn: AsyncConnection, event_id: str, *, lock: bool = False) -> dict[str, Any] | None:
    try:
        key = uuid.UUID(event_id)
    except ValueError:
        return None
    row = (await conn.execute(text(f"SELECT {_EVENT} FROM agent.revocation_event WHERE event_id = :e{' FOR UPDATE' if lock else ''}"), {"e": key})).first()
    return _row(row) if row else None


async def list_events(conn: AsyncConnection, *, open_only: bool, limit: int) -> list[dict[str, Any]]:
    rows = await conn.execute(
        text(f"SELECT {_EVENT} FROM agent.revocation_event WHERE (NOT :o OR closed_at IS NULL) ORDER BY epoch DESC LIMIT :n"), {"o": open_only, "n": limit}
    )
    return [_row(x) for x in rows]


async def close_event(conn: AsyncConnection, event_id: str, *, by: str, note: str, now: datetime) -> bool:
    res = await conn.execute(
        text("UPDATE agent.revocation_event SET closed_by = :b, closed_at = :n, close_note = :note WHERE event_id = :e AND closed_at IS NULL"),
        {"b": uuid.UUID(by), "n": now, "note": note, "e": uuid.UUID(event_id)},
    )
    return bool(res.rowcount)


async def events_covering(conn: AsyncConnection, agent_id: str) -> list[dict[str, Any]]:
    """Every stop that reached this agent, newest first, open or closed."""
    rows = await conn.execute(
        text(f"SELECT {_EVENT} FROM agent.revocation_event WHERE targets->'agent_ids' @> CAST(:j AS jsonb) ORDER BY epoch DESC"), {"j": json.dumps([agent_id])}
    )
    return [_row(x) for x in rows]
