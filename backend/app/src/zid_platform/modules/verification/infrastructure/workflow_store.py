"""Workflow versions and runs (DTV-30; CR-099). Fixed statements only; row-level security scopes every read to the caller's company."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.verification.domain.workflow_run import Event, RunState, Status

VERSION_COLUMNS = "workflow_id, version, definition, content_hash, status, author, approved_by, approved_at, created_at"
RUN_COLUMNS = (
    "run_id, workflow_id, version, content_hash, subject_principal_id, started_by, status, step, results, awaiting_check, awaiting_role, outcome, "
    "events, notifications, created_at, updated_at, erased_at, row_version"
)


def _row(r: Any) -> dict[str, Any]:
    return dict(r._mapping)


async def insert_version(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    workflow_id: str,
    version: str,
    definition: dict[str, Any],
    content_hash: str,
    author: str,
) -> dict[str, Any]:
    row = (
        await conn.execute(
            text(
                "INSERT INTO verify.workflow_version (tenant_id, workflow_id, version, definition, content_hash, author) "
                "VALUES (:t, :w, :v, CAST(:d AS jsonb), :h, :a) RETURNING " + VERSION_COLUMNS
            ),
            {
                "t": uuid.UUID(tenant_id),
                "w": workflow_id,
                "v": version,
                "d": json.dumps(definition),
                "h": content_hash,
                "a": uuid.UUID(author),
            },
        )
    ).first()
    assert row is not None
    return _row(row)


async def load_version(
    conn: AsyncConnection, workflow_id: str, version: str
) -> dict[str, Any] | None:
    row = (
        await conn.execute(
            text(
                "SELECT "
                + VERSION_COLUMNS
                + " FROM verify.workflow_version WHERE workflow_id = :w AND version = :v"
            ),
            {"w": workflow_id, "v": version},
        )
    ).first()
    return _row(row) if row else None


async def list_versions(conn: AsyncConnection, status: str | None) -> list[dict[str, Any]]:
    rows = (
        await conn.execute(
            text(
                "SELECT "
                + VERSION_COLUMNS
                + " FROM verify.workflow_version WHERE (CAST(:s AS text) IS NULL OR status = :s) "
                "ORDER BY workflow_id, created_at DESC LIMIT 200"
            ),
            {"s": status},
        )
    ).all()
    return [_row(r) for r in rows]


async def approve_version(
    conn: AsyncConnection, workflow_id: str, version: str, approver: str, now: datetime
) -> None:
    await conn.execute(
        text(
            "UPDATE verify.workflow_version SET status = 'APPROVED', approved_by = :a, approved_at = :now "
            "WHERE workflow_id = :w AND version = :v AND status = 'DRAFT'"
        ),
        {"a": uuid.UUID(approver), "now": now, "w": workflow_id, "v": version},
    )


async def retire_version(conn: AsyncConnection, workflow_id: str, version: str) -> None:
    await conn.execute(
        text(
            "UPDATE verify.workflow_version SET status = 'RETIRED' WHERE workflow_id = :w AND version = :v AND status = 'APPROVED'"
        ),
        {"w": workflow_id, "v": version},
    )


def _events(state: RunState) -> str:
    return json.dumps([{"step": e.step, "kind": e.kind, "detail": e.detail} for e in state.events])


async def insert_run(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    workflow_id: str,
    version: str,
    subject: str,
    started_by: str,
    state: RunState,
) -> dict[str, Any]:
    row = (
        await conn.execute(
            text(
                "INSERT INTO verify.workflow_run (run_id, tenant_id, workflow_id, version, content_hash, subject_principal_id, started_by, status, step, "
                "results, awaiting_check, awaiting_role, outcome, events, notifications) VALUES (:r, :t, :w, :v, :h, :s, :by, :st, :step, "
                "CAST(:res AS jsonb), :ac, :ar, :o, CAST(:ev AS jsonb), CAST(:n AS jsonb)) RETURNING "
                + RUN_COLUMNS
            ),
            {
                "r": uuid.uuid4(),
                "t": uuid.UUID(tenant_id),
                "w": workflow_id,
                "v": version,
                "h": state.workflow_hash,
                "s": uuid.UUID(subject),
                "by": uuid.UUID(started_by),
                "st": state.status.value,
                "step": state.step,
                "res": json.dumps(dict(state.results)),
                "ac": state.awaiting_check,
                "ar": state.awaiting_role,
                "o": state.outcome,
                "ev": _events(state),
                "n": json.dumps(list(state.notifications)),
            },
        )
    ).first()
    assert row is not None
    return _row(row)


async def load_run(
    conn: AsyncConnection, run_id: str, *, lock: bool = False
) -> dict[str, Any] | None:
    sql = "SELECT " + RUN_COLUMNS + " FROM verify.workflow_run WHERE run_id = :r"
    row = (
        await conn.execute(text(sql + (" FOR UPDATE" if lock else "")), {"r": uuid.UUID(run_id)})
    ).first()
    return _row(row) if row else None


async def save_run(conn: AsyncConnection, run_id: str, state: RunState, now: datetime) -> None:
    await conn.execute(
        text(
            "UPDATE verify.workflow_run SET status = :st, step = :step, results = CAST(:res AS jsonb), awaiting_check = :ac, awaiting_role = :ar, "
            "outcome = :o, events = CAST(:ev AS jsonb), notifications = CAST(:n AS jsonb), updated_at = :now, row_version = row_version + 1 "
            "WHERE run_id = :r"
        ),
        {
            "st": state.status.value,
            "step": state.step,
            "res": json.dumps(dict(state.results)),
            "ac": state.awaiting_check,
            "ar": state.awaiting_role,
            "o": state.outcome,
            "ev": _events(state),
            "n": json.dumps(list(state.notifications)),
            "now": now,
            "r": uuid.UUID(run_id),
        },
    )


async def list_runs(conn: AsyncConnection, status: str | None, limit: int) -> list[dict[str, Any]]:
    rows = (
        await conn.execute(
            text(
                "SELECT run_id, workflow_id, version, subject_principal_id, status, outcome, awaiting_check, awaiting_role, created_at, updated_at "
                "FROM verify.workflow_run WHERE (CAST(:s AS text) IS NULL OR status = :s) AND erased_at IS NULL "
                "ORDER BY created_at DESC, run_id LIMIT :n"
            ),
            {"s": status, "n": limit},
        )
    ).all()
    return [_row(r) for r in rows]


async def runs_for_subject(conn: AsyncConnection, subject: str) -> list[dict[str, Any]]:
    rows = (
        await conn.execute(
            text(
                "SELECT run_id, created_at FROM verify.workflow_run WHERE subject_principal_id = :p AND erased_at IS NULL"
            ),
            {"p": uuid.UUID(subject)},
        )
    ).all()
    return [_row(r) for r in rows]


async def erase_run(conn: AsyncConnection, run_id: str, now: datetime) -> None:
    """Governed erasure: the results and events go, the outcome stays, and the run can no longer move."""
    await conn.execute(
        text(
            "UPDATE verify.workflow_run SET results = '{}'::jsonb, events = '[]'::jsonb, notifications = '[]'::jsonb, erased_at = :now, updated_at = :now, "
            "row_version = row_version + 1 WHERE run_id = :r"
        ),
        {"now": now, "r": uuid.UUID(run_id)},
    )


def state_of(row: dict[str, Any]) -> RunState:
    return RunState(
        workflow_hash=row["content_hash"],
        status=Status(row["status"]),
        step=row["step"],
        results=dict(row["results"] or {}),
        awaiting_check=row["awaiting_check"],
        awaiting_role=row["awaiting_role"],
        outcome=row["outcome"],
        events=tuple(
            Event(e["step"], e["kind"], e.get("detail", "")) for e in (row["events"] or [])
        ),
        notifications=tuple(row["notifications"] or ()),
    )
