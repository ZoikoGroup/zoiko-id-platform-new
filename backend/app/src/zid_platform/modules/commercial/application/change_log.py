"""The commercial change log: an append-only hash chain of every commercial change (ZID-COM-001 §24; CR-030).

Kept apart from the other services so any of them can append to it without an import cycle.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.commercial.domain import assurance as a


async def log_change(
    conn: AsyncConnection,
    *,
    kind: str,
    ref: str,
    tenant_id: str | None,
    actor: str | None,
    reason: str | None = None,
    detail: Mapping[str, Any] | None = None,
) -> int:
    """Append one entry to the commercial change log. The head row is locked, so the chain is gap-free."""
    head = (
        await conn.execute(
            text("SELECT last_seq, last_hash FROM commercial.change_log_head FOR UPDATE")
        )
    ).one()
    seq = int(head[0]) + 1
    body = {
        "kind": kind,
        "ref": ref,
        "tenant": tenant_id,
        "actor": actor,
        "reason": reason,
        "detail": dict(detail or {}),
    }
    entry = a.digest(body)
    chain = a.link(head[1], seq, entry)
    await conn.execute(
        text(
            "INSERT INTO commercial.change_log (seq, kind, ref, tenant_id, actor, reason, detail, entry_digest, "
            "chain_hash) VALUES (:s, :k, :r, :t, :a, :why, CAST(:d AS jsonb), :e, :c)"
        ),
        {
            "s": seq,
            "k": kind,
            "r": ref,
            "t": uuid.UUID(tenant_id) if tenant_id else None,
            "a": uuid.UUID(actor) if actor else None,
            "why": reason,
            "d": a.canonical(dict(detail or {})),
            "e": entry,
            "c": chain,
        },
    )
    await conn.execute(
        text("UPDATE commercial.change_log_head SET last_seq = :s, last_hash = :c"),
        {"s": seq, "c": chain},
    )
    return seq


async def verify_change_log(conn: AsyncConnection) -> dict[str, Any]:
    rows = (
        await conn.execute(
            text(
                "SELECT seq, kind, ref, tenant_id, actor, reason, detail, entry_digest, chain_hash "
                "FROM commercial.change_log ORDER BY seq"
            )
        )
    ).all()
    tampered = None
    for r in rows:
        body = {
            "kind": r[1],
            "ref": r[2],
            "tenant": str(r[3]) if r[3] else None,
            "actor": str(r[4]) if r[4] else None,
            "reason": r[5],
            "detail": r[6],
        }
        if a.digest(body) != r[7]:
            tampered = int(r[0])
            break
    broken = a.verify([(int(r[0]), str(r[7]), str(r[8])) for r in rows])
    first = min(x for x in (tampered, broken) if x is not None) if (tampered or broken) else None
    return {"entries": len(rows), "intact": first is None, "first_broken": first}
