"""SQL for tool actions and their receipts (CR-169; machine_0013). Tenant transactions with RLS on; nothing here decides anything."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

_ACTION = (
    "action_id, tenant_id, agent_principal_id, tool_id, audience, resource, args_sha256, decision_id, chain_commitment, agent_chain, mandate_id, mandate_hash, reservation_id, state, revision, "
    "downstream_ref, reported_by, reported_at, created_at, updated_at"
)


def _row(x: Any) -> dict[str, Any]:
    return dict(x._mapping)


async def insert_action(
    conn: AsyncConnection, tenant_id: str, *, action_id: str, agent_id: str, tool_id: str, audience: str, resource: str, args_sha256: str, decision_id: str,
    chain_commitment: str, agent_chain: list[str], mandate_id: str | None, mandate_hash: str | None, reservation_id: str | None, now: datetime,
) -> None:  # fmt: skip
    await conn.execute(
        text(
            "INSERT INTO agent.tool_action (action_id, tenant_id, agent_principal_id, tool_id, audience, resource, args_sha256, decision_id, chain_commitment, agent_chain, mandate_id, mandate_hash, reservation_id, created_at, updated_at) "
            "VALUES (:a, :t, :ag, :tool, :aud, :res, :args, :d, :cc, :ch, :m, :mh, :r, :n, :n)"
        ),
        {
            "a": uuid.UUID(action_id), "t": uuid.UUID(tenant_id), "ag": uuid.UUID(agent_id), "tool": tool_id, "aud": uuid.UUID(audience), "res": resource, "args": args_sha256,
            "d": decision_id, "cc": chain_commitment, "ch": agent_chain, "mh": mandate_hash, "m": uuid.UUID(mandate_id) if mandate_id else None, "r": uuid.UUID(reservation_id) if reservation_id else None, "n": now,
        },
    )  # fmt: skip


async def get_action(
    conn: AsyncConnection, action_id: str, *, lock: bool = False
) -> dict[str, Any] | None:
    row = (
        await conn.execute(
            text(
                f"SELECT {_ACTION} FROM agent.tool_action WHERE action_id = :a{' FOR UPDATE' if lock else ''}"
            ),
            {"a": uuid.UUID(action_id)},
        )
    ).first()
    return _row(row) if row else None


async def mark_dispatched(conn: AsyncConnection, action_id: str, now: datetime) -> bool:
    """AUTHORIZED to DISPATCHED, once. False when it was not in that state."""
    res = await conn.execute(
        text(
            "UPDATE agent.tool_action SET state = 'DISPATCHED', revision = revision + 1, updated_at = :n WHERE action_id = :a AND state = 'AUTHORIZED'"
        ),
        {"a": uuid.UUID(action_id), "n": now},
    )
    return bool(res.rowcount)


async def set_state(
    conn: AsyncConnection,
    action_id: str,
    *,
    expected_revision: int,
    state: str,
    reported_by: str,
    downstream_ref: str | None,
    now: datetime,
) -> bool:
    res = await conn.execute(
        text(
            "UPDATE agent.tool_action SET state = :s, revision = revision + 1, reported_by = CAST(:b AS uuid), reported_at = :n, "
            "downstream_ref = COALESCE(:r, downstream_ref), updated_at = :n WHERE action_id = :a AND revision = :v"
        ),
        {
            "s": state,
            "b": reported_by,
            "n": now,
            "r": downstream_ref,
            "a": uuid.UUID(action_id),
            "v": expected_revision,
        },
    )
    return bool(res.rowcount)


async def list_actions(
    conn: AsyncConnection, *, state: str | None, agent_id: str | None, limit: int
) -> list[dict[str, Any]]:
    rows = await conn.execute(
        text(
            f"SELECT {_ACTION} FROM agent.tool_action WHERE (CAST(:s AS text) IS NULL OR state = :s) AND (CAST(:g AS uuid) IS NULL OR agent_principal_id = CAST(:g AS uuid)) "
            "ORDER BY updated_at DESC, action_id DESC LIMIT :n"
        ),
        {"s": state, "g": agent_id, "n": limit},
    )
    return [_row(x) for x in rows]


async def add_receipt(
    conn: AsyncConnection, tenant_id: str, *, action_id: str, revision: int, state: str, payload: dict[str, Any], digest: str, signature: str, key_id: str | None, issued_at: datetime,
) -> str:  # fmt: skip
    rid = uuid.uuid4()
    await conn.execute(
        text(
            "INSERT INTO agent.tool_action_receipt (receipt_id, action_id, tenant_id, revision, state, payload, digest, signature, key_id, issued_at) "
            "VALUES (:r, :a, :t, :v, :s, CAST(:p AS jsonb), :d, :sig, :k, :i)"
        ),
        {
            "r": rid,
            "a": uuid.UUID(action_id),
            "t": uuid.UUID(tenant_id),
            "v": revision,
            "s": state,
            "p": json.dumps(payload),
            "d": digest,
            "sig": signature,
            "k": key_id,
            "i": issued_at,
        },
    )
    return str(rid)


_RECEIPT = "receipt_id, action_id, revision, state, payload, digest, signature, key_id, issued_at"


async def receipts_for(conn: AsyncConnection, action_id: str) -> list[dict[str, Any]]:
    rows = await conn.execute(
        text(
            f"SELECT {_RECEIPT} FROM agent.tool_action_receipt WHERE action_id = :a ORDER BY revision"
        ),
        {"a": uuid.UUID(action_id)},
    )
    return [_row(x) for x in rows]


# ------------------------------------------------------------------------------- the dry run of a stop (what belongs to this module)


async def live_grants_of(conn: AsyncConnection, agent_ids: list[str], now: datetime) -> int:
    return int(
        (
            await conn.execute(
                text(
                    "SELECT count(*) FROM agent.tool_grant WHERE agent_principal_id = ANY(:a) AND consumed_at IS NULL AND expires_at > :n"
                ),
                {"a": [uuid.UUID(x) for x in agent_ids], "n": now},
            )
        ).scalar_one()
    )


async def unresolved_actions_of(conn: AsyncConnection, agent_ids: list[str]) -> int:
    return int(
        (
            await conn.execute(
                text(
                    "SELECT count(*) FROM agent.tool_action WHERE agent_principal_id = ANY(:a) AND state IN ('DISPATCHED', 'UNKNOWN')"
                ),
                {"a": [uuid.UUID(x) for x in agent_ids]},
            )
        ).scalar_one()
    )


async def unresolved_actions(conn: AsyncConnection, *, limit: int) -> list[dict[str, Any]]:
    rows = await conn.execute(
        text(
            f"SELECT {_ACTION} FROM agent.tool_action WHERE state IN ('DISPATCHED', 'UNKNOWN') ORDER BY updated_at LIMIT :n"
        ),
        {"n": limit},
    )
    return [_row(x) for x in rows]
