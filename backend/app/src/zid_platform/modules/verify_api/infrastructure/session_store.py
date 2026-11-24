"""Verify API sessions, decisions and idempotency keys in PostgreSQL (CR-150). Tenant rows are scoped by row-level security; the hosted page finds its session by the hash of the launch token inside
`store.auth_scope`, exactly as the token endpoint finds a credential."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

_SESSION = (
    "session_id, tenant_id, application_id, environment, customer_reference, workflow_id, workflow_version, country, region, purpose_code, locale, return_origin, "
    "metadata, status, launch_expires_at, created_at, opened_at, completed_at, expires_at, recheck_of"
)
_DECISION = "decision_id, session_id, decision_version, supersedes_decision_id, outcome, checks, reason_codes, sandbox, policy_version, region, completed_at"


def _row(r: Any) -> dict[str, Any]:
    return dict(r._mapping)


async def insert_session(
    conn: AsyncConnection,
    *,
    session_id: str,
    tenant_id: str,
    application_id: str,
    environment: str,
    clean: dict[str, Any],
    status: str,
    token_hash: bytes,
    launch_expires_at: datetime,
    expires_at: datetime,
    recheck_of: str | None = None,
) -> None:
    await conn.execute(
        text(
            "INSERT INTO verify.api_session (session_id, tenant_id, application_id, environment, customer_reference, workflow_id, workflow_version, country, region, "
            "purpose_code, locale, return_origin, metadata, status, launch_token_hash, launch_expires_at, expires_at, recheck_of) "
            "VALUES (:sid, :t, :a, :env, :ref, :wf, :wv, :c, :r, :p, :l, :o, CAST(:m AS jsonb), :st, :h, :lx, :x, :rc)"
        ),
        {
            "sid": session_id,
            "t": uuid.UUID(tenant_id),
            "a": uuid.UUID(application_id),
            "env": environment,
            "ref": clean["customer_reference"],
            "wf": clean["workflow_id"],
            "wv": clean["workflow_version"],
            "c": clean["country"],
            "r": clean["region"],
            "p": clean["purpose_code"],
            "l": clean["locale"],
            "o": clean["return_origin"],
            "m": json.dumps(clean["metadata"]),
            "st": status,
            "h": token_hash,
            "lx": launch_expires_at,
            "x": expires_at,
            "rc": recheck_of,
        },
    )


async def get_session(
    conn: AsyncConnection, session_id: str, application_id: str, *, lock: bool = False
) -> dict[str, Any] | None:
    """A session, only if it belongs to this application: another application of the same company never sees it."""
    row = (
        await conn.execute(
            text(
                f"SELECT {_SESSION} FROM verify.api_session WHERE session_id = :s AND application_id = :a"
                + (" FOR UPDATE" if lock else "")
            ),
            {"s": session_id, "a": uuid.UUID(application_id)},
        )
    ).first()
    return _row(row) if row else None


async def list_sessions(
    conn: AsyncConnection,
    application_id: str,
    *,
    status: str | None,
    reference: str | None,
    created_from: datetime | None,
    created_to: datetime | None,
    after: tuple[datetime, str] | None,
    limit: int,
) -> list[dict[str, Any]]:
    """Newest first, paged by a cursor (the last row's time and id) so a page never repeats or skips. Only fixed filters; nothing is searched by name."""
    where = ["application_id = :a"]
    p: dict[str, Any] = {"a": uuid.UUID(application_id), "n": limit}
    if status:
        where.append("status = :st")
        p["st"] = status
    if reference:
        where.append("customer_reference = :ref")
        p["ref"] = reference
    if created_from:
        where.append("created_at >= :cf")
        p["cf"] = created_from
    if created_to:
        where.append("created_at < :ct")
        p["ct"] = created_to
    if after:
        where.append("(created_at, session_id) < (:ac, :asid)")
        p["ac"], p["asid"] = after
    rows = (
        await conn.execute(
            text(
                f"SELECT {_SESSION} FROM verify.api_session WHERE {' AND '.join(where)} ORDER BY created_at DESC, session_id DESC LIMIT :n"
            ),
            p,
        )
    ).all()
    return [_row(r) for r in rows]


async def set_launch(
    conn: AsyncConnection, session_id: str, token_hash: bytes, expires_at: datetime
) -> None:
    await conn.execute(
        text(
            "UPDATE verify.api_session SET launch_token_hash = :h, launch_expires_at = :x WHERE session_id = :s"
        ),
        {"s": session_id, "h": token_hash, "x": expires_at},
    )


async def end_launch(conn: AsyncConnection, session_id: str) -> None:
    """The link stops working: a finished or canceled session has nothing left to open."""
    await conn.execute(
        text(
            "UPDATE verify.api_session SET launch_token_hash = NULL, launch_expires_at = NULL WHERE session_id = :s"
        ),
        {"s": session_id},
    )


async def set_status(
    conn: AsyncConnection,
    session_id: str,
    status: str,
    now: datetime,
    *,
    opened: bool = False,
    completed: bool = False,
) -> None:
    await conn.execute(
        text(
            "UPDATE verify.api_session SET status = :st, opened_at = CASE WHEN :o THEN :n ELSE opened_at END, "
            "completed_at = CASE WHEN :c THEN :n ELSE completed_at END WHERE session_id = :s"
        ),
        {"s": session_id, "st": status, "n": now, "o": opened, "c": completed},
    )


async def session_by_launch(conn: AsyncConnection, token_hash: bytes) -> dict[str, Any] | None:
    """Inside `store.auth_scope`: the session a launch link belongs to, with its application's name, by the hash of the link's token."""
    row = (
        await conn.execute(
            text(
                f"SELECT {', '.join('s.' + c.strip() for c in _SESSION.split(','))}, a.name AS application_name, a.status AS application_status, a.branding AS branding, a.redirect_origins AS application_origins "
                "FROM verify.api_session s JOIN verify.api_application a USING (application_id) WHERE s.launch_token_hash = :h"
            ),
            {"h": token_hash},
        )
    ).first()
    return _row(row) if row else None


# ------------------------------------------------------------------------------- decisions


async def latest_decision(conn: AsyncConnection, session_id: str) -> dict[str, Any] | None:
    row = (
        await conn.execute(
            text(
                f"SELECT {_DECISION} FROM verify.api_decision WHERE session_id = :s ORDER BY decision_version DESC LIMIT 1"
            ),
            {"s": session_id},
        )
    ).first()
    return _row(row) if row else None


async def insert_decision(
    conn: AsyncConnection,
    *,
    decision_id: str,
    session_id: str,
    tenant_id: str,
    version: int,
    supersedes: str | None,
    outcome: str,
    checks: list[dict[str, str]],
    reason_codes: list[str],
    sandbox: bool,
    policy_version: str,
    region: str,
    completed_at: datetime,
) -> None:
    await conn.execute(
        text(
            "INSERT INTO verify.api_decision (decision_id, session_id, tenant_id, decision_version, supersedes_decision_id, outcome, checks, reason_codes, sandbox, policy_version, region, completed_at) "
            "VALUES (:d, :s, :t, :v, :sup, :o, CAST(:c AS jsonb), :rc, :sb, :pv, :r, :x)"
        ),
        {
            "d": decision_id,
            "s": session_id,
            "t": uuid.UUID(tenant_id),
            "v": version,
            "sup": supersedes,
            "o": outcome,
            "c": json.dumps(checks),
            "rc": reason_codes,
            "sb": sandbox,
            "pv": policy_version,
            "r": region,
            "x": completed_at,
        },
    )


# ------------------------------------------------------------------------------- idempotency


async def get_idempotency(
    conn: AsyncConnection, application_id: str, key: str
) -> dict[str, Any] | None:
    row = (
        await conn.execute(
            text(
                "SELECT request_hash, session_id, created_at FROM verify.api_idempotency WHERE application_id = :a AND idem_key = :k FOR UPDATE"
            ),
            {"a": uuid.UUID(application_id), "k": key},
        )
    ).first()
    return _row(row) if row else None


async def insert_idempotency(
    conn: AsyncConnection,
    *,
    application_id: str,
    key: str,
    tenant_id: str,
    request_hash: bytes,
    session_id: str,
) -> None:
    await conn.execute(
        text(
            "INSERT INTO verify.api_idempotency (application_id, idem_key, tenant_id, request_hash, session_id) VALUES (:a, :k, :t, :h, :s)"
        ),
        {
            "a": uuid.UUID(application_id),
            "k": key,
            "t": uuid.UUID(tenant_id),
            "h": request_hash,
            "s": session_id,
        },
    )


async def replace_idempotency(
    conn: AsyncConnection, *, application_id: str, key: str, request_hash: bytes, session_id: str
) -> None:
    """A key older than its window starts over: it now stands for this new request."""
    await conn.execute(
        text(
            "UPDATE verify.api_idempotency SET request_hash = :h, session_id = :s, created_at = now() WHERE application_id = :a AND idem_key = :k"
        ),
        {"a": uuid.UUID(application_id), "k": key, "h": request_hash, "s": session_id},
    )


# ------------------------------------------------------------------------------- the company's own view across its applications (a signed-in administrator, row-level security to the company)


async def get_session_any(
    conn: AsyncConnection, session_id: str, *, lock: bool = False
) -> dict[str, Any] | None:
    """A session of this company, whichever application made it."""
    row = (
        await conn.execute(
            text(
                f"SELECT {_SESSION} FROM verify.api_session WHERE session_id = :s"
                + (" FOR UPDATE" if lock else "")
            ),
            {"s": session_id},
        )
    ).first()
    return _row(row) if row else None


async def search_sessions(
    conn: AsyncConnection,
    *,
    application_id: str | None,
    environment: str | None,
    status: str | None,
    reference: str | None,
    after: tuple[datetime, str] | None,
    limit: int,
) -> list[dict[str, Any]]:
    """Newest first across the company's applications, by fixed filters only. The reference is matched exactly, never searched as text."""
    where = ["true"]
    p: dict[str, Any] = {"n": limit}
    if application_id:
        where.append("application_id = :a")
        p["a"] = uuid.UUID(application_id)
    if environment:
        where.append("environment = :env")
        p["env"] = environment
    if status:
        where.append("status = :st")
        p["st"] = status
    if reference:
        where.append("customer_reference = :ref")
        p["ref"] = reference
    if after:
        where.append("(created_at, session_id) < (:ac, :asid)")
        p["ac"], p["asid"] = after
    rows = (
        await conn.execute(
            text(
                f"SELECT {_SESSION} FROM verify.api_session WHERE {' AND '.join(where)} ORDER BY created_at DESC, session_id DESC LIMIT :n"
            ),
            p,
        )
    ).all()
    return [_row(r) for r in rows]


async def list_decisions(conn: AsyncConnection, session_id: str) -> list[dict[str, Any]]:
    """Every decision of a session, oldest first: a later one supersedes an earlier one and the earlier one stays."""
    rows = (
        await conn.execute(
            text(
                f"SELECT {_DECISION} FROM verify.api_decision WHERE session_id = :s ORDER BY decision_version"
            ),
            {"s": session_id},
        )
    ).all()
    return [_row(r) for r in rows]


async def rechecks_of(conn: AsyncConnection, session_id: str) -> list[dict[str, Any]]:
    """The sessions that follow this one as rechecks, oldest first."""
    rows = (
        await conn.execute(
            text(
                "SELECT session_id, status, created_at FROM verify.api_session WHERE recheck_of = :s ORDER BY created_at"
            ),
            {"s": session_id},
        )
    ).all()
    return [_row(r) for r in rows]
