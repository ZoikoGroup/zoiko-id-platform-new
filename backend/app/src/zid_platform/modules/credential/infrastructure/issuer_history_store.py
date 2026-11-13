"""Issuer statements in PostgreSQL (ZID-RCG-003 RCG-G04; CR-075). Platform configuration: not tenant data, no RLS.

Append-only: recording a statement never edits another, and an earlier one keeps answering for the moment it was the latest. The domain rules live
in `domain/issuer_history.py`; the table repeats the ones that must hold even for a caller that skips the domain code (migration credential_0013).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.credential.domain import trust_graph as tg
from zid_platform.modules.credential.domain.issuer_history import (
    Kind,
    Statement,
    check_statement,
    registry_as_known_at,
)

_COLUMNS = "issuer_id, seq, statement_id, kind, reason, record, source_url, source_hash, evidence_locator, recorded_by, recorded_at"


def _statement(r: Any) -> Statement:
    record = r.record if isinstance(r.record, dict) else json.loads(r.record)
    return Statement(
        statement_id=r.statement_id,
        issuer_id=r.issuer_id,
        seq=r.seq,
        kind=Kind(r.kind),
        reason=r.reason,
        record=record,
        source_url=r.source_url,
        source_hash=r.source_hash,
        evidence_locator=r.evidence_locator,
        recorded_by=r.recorded_by,
        recorded_at=r.recorded_at,
    )


async def history(conn: AsyncConnection, issuer_id: str) -> list[Statement]:
    """Every statement about the issuer, oldest first."""
    rows = (
        await conn.execute(
            text(f"SELECT {_COLUMNS} FROM cred.issuer_statement WHERE issuer_id = :i ORDER BY seq"),
            {"i": issuer_id},
        )
    ).all()
    return [_statement(r) for r in rows]


async def record(
    conn: AsyncConnection, statement: Statement, *, now: datetime
) -> tuple[Statement, list[str]]:
    """Check the statement against the domain rules and the ones before it, then append it. Returns it with the retroactive changes a
    correction makes."""
    changes = check_statement(statement, now=now, previous=await history(conn, statement.issuer_id))
    await conn.execute(
        text(
            f"INSERT INTO cred.issuer_statement ({_COLUMNS}) VALUES (:i, :q, :id, :k, :r, CAST(:rec AS jsonb), :su, :sh, :el, :by, :at)"
        ),
        {
            "i": statement.issuer_id,
            "q": statement.seq,
            "id": statement.statement_id,
            "k": statement.kind.value,
            "r": statement.reason,
            "rec": json.dumps(dict(statement.record), default=str, sort_keys=True),
            "su": statement.source_url,
            "sh": statement.source_hash,
            "el": statement.evidence_locator,
            "by": statement.recorded_by,
            "at": statement.recorded_at,
        },
    )
    return statement, changes


async def issuer_ids(conn: AsyncConnection, *, limit: int = 500) -> list[str]:
    rows = (
        await conn.execute(
            text(
                "SELECT DISTINCT issuer_id FROM cred.issuer_statement ORDER BY issuer_id LIMIT :n"
            ),
            {"n": limit},
        )
    ).all()
    return [r[0] for r in rows]


async def registry_at(
    conn: AsyncConnection, at: datetime, base: tg.Registry | None = None
) -> tg.Registry:
    """The trust-graph registry the ledger gives for the moment `at`, layered over `base` (the configured registry). One query: the latest
    statement of each issuer recorded by then."""
    rows = (
        await conn.execute(
            text(
                f"SELECT DISTINCT ON (issuer_id) {_COLUMNS} FROM cred.issuer_statement "
                "WHERE recorded_at <= :at ORDER BY issuer_id, seq DESC"
            ),
            {"at": at},
        )
    ).all()
    histories: Mapping[str, list[Statement]] = {r.issuer_id: [_statement(r)] for r in rows}
    return registry_as_known_at(histories, at, base)
