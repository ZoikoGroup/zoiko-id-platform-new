"""Identity sessions and DPoP replay records in PostgreSQL (ZID-DATA-001 §5.3; ZID-AUTH-001 §5, §7.1, §16).

All calls run inside a tenant transaction (RLS). State changes emit outbox events so session
revocation propagates (AUTH §16, §23).
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection
from zid_outbox import emit

from zid_platform.modules.session.domain.session import (
    Session,
    SessionError,
    SessionStatus,
    transition,
)

_COLS = (
    "session_id, tenant_id, principal_id, auth_time, authentication_context, aal, "
    "observed_security_revision, risk_class, status, expires_at, last_seen_at"
)


def _session(r: object) -> Session:
    m = r._mapping  # type: ignore[attr-defined]
    return Session(
        session_id=str(m["session_id"]),
        tenant_id=str(m["tenant_id"]),
        principal_id=str(m["principal_id"]),
        auth_time=m["auth_time"],
        authentication_context=m["authentication_context"],
        aal=m["aal"],
        observed_security_revision=m["observed_security_revision"],
        risk_class=m["risk_class"],
        status=SessionStatus(m["status"]),
        expires_at=m["expires_at"],
        last_seen_at=m["last_seen_at"],
    )


async def create_session(conn: AsyncConnection, s: Session) -> None:
    await conn.execute(
        text(
            f"INSERT INTO authn.session ({_COLS}) VALUES (:sid, :t, :p, :at, :ctx, :aal, :rev, :risk, "
            "CAST(:st AS authn.session_status), :exp, :seen)"
        ),
        {
            "sid": uuid.UUID(s.session_id),
            "t": uuid.UUID(s.tenant_id),
            "p": uuid.UUID(s.principal_id),
            "at": s.auth_time,
            "ctx": s.authentication_context,
            "aal": s.aal,
            "rev": s.observed_security_revision,
            "risk": s.risk_class,
            "st": s.status.value,
            "exp": s.expires_at,
            "seen": s.last_seen_at,
        },
    )
    await emit(
        conn,
        name="session.created",
        tenant_id=s.tenant_id,
        aggregate_type="session",
        aggregate_id=s.session_id,
        aggregate_version=1,
        data={"session_id": s.session_id, "principal_id": s.principal_id, "aal": s.aal},
    )


async def load_session(conn: AsyncConnection, session_id: str) -> Session | None:
    try:
        sid = uuid.UUID(session_id)
    except ValueError:
        return None
    row = (
        await conn.execute(
            text(f"SELECT {_COLS} FROM authn.session WHERE session_id = :s"), {"s": sid}
        )
    ).first()
    return _session(row) if row else None


async def touch(conn: AsyncConnection, session_id: str, now: datetime) -> None:
    await conn.execute(
        text(
            "UPDATE authn.session SET last_seen_at = greatest(coalesce(last_seen_at, :now), :now) "
            "WHERE session_id = :s AND status = 'ACTIVE'"
        ),
        {"s": uuid.UUID(session_id), "now": now},
    )


async def set_status(
    conn: AsyncConnection, session_id: str, target: SessionStatus, *, reason: str
) -> Session:
    current = await load_session(conn, session_id)
    if current is None:
        raise SessionError("SESSION_NOT_FOUND")
    if current.status is target:
        return current
    transition(current.status, target)
    result = await conn.execute(
        text(
            "UPDATE authn.session SET status = CAST(:st AS authn.session_status), "
            "revoked_at = CASE WHEN :st = 'REVOKED' THEN now() ELSE revoked_at END "
            "WHERE session_id = :s AND status = CAST(:old AS authn.session_status)"
        ),
        {"st": target.value, "s": uuid.UUID(session_id), "old": current.status.value},
    )
    if result.rowcount != 1:
        raise SessionError("CONCURRENT_MODIFICATION")
    if target is SessionStatus.REVOKED:  # the one session change other cells must act on (AUTH §16)
        await emit(
            conn,
            name="session.revoked",
            tenant_id=current.tenant_id,
            aggregate_type="session",
            aggregate_id=session_id,
            aggregate_version=2,
            data={"session_id": session_id, "principal_id": current.principal_id, "reason": reason},
        )
    return current


async def revoke_principal_sessions(
    conn: AsyncConnection, principal_id: str, *, reason: str
) -> int:
    """Global logout / compromise response: every live session of the principal (AUTH §16)."""
    rows = (
        await conn.execute(
            text(
                "SELECT session_id FROM authn.session WHERE principal_id = :p "
                "AND status IN ('ACTIVE', 'REAUTH_REQUIRED')"
            ),
            {"p": uuid.UUID(principal_id)},
        )
    ).all()
    for r in rows:
        await set_status(conn, str(r.session_id), SessionStatus.REVOKED, reason=reason)
    return len(rows)


async def record_dpop_proof(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    jkt: str,
    jti: str,
    issued_at: datetime,
    window: timedelta,
) -> bool:
    """True the first time this (key, jti) is seen inside its window; False means a replay."""
    result = await conn.execute(
        text(
            "INSERT INTO authn.dpop_proof_jti (tenant_id, jkt, jti, expires_at) VALUES (:t, :k, :j, :exp) "
            "ON CONFLICT DO NOTHING"
        ),
        {"t": uuid.UUID(tenant_id), "k": jkt, "j": jti, "exp": issued_at + window},
    )
    return result.rowcount == 1


async def issue_handle(conn: AsyncConnection, session_id: str) -> str:
    """Mint the browser handle (cookie value) of an authenticated session, once (AUTH §5.1, AUTH-18).

    Only the SHA-256 is stored; a handle is never set before authentication and never replaced, so a
    pre-authentication identifier cannot be fixed onto the session."""
    handle = secrets.token_urlsafe(32)
    result = await conn.execute(
        text(
            "UPDATE authn.session SET handle_hash = :h "
            "WHERE session_id = :s AND handle_hash IS NULL AND status = 'ACTIVE'"
        ),
        {"h": hashlib.sha256(handle.encode()).digest(), "s": uuid.UUID(session_id)},
    )
    if result.rowcount != 1:
        raise SessionError("SESSION_HANDLE_UNAVAILABLE")
    return handle


async def load_session_by_handle(conn: AsyncConnection, handle: str) -> Session | None:
    if not handle or len(handle) > 128:
        return None
    row = (
        await conn.execute(
            text(f"SELECT {_COLS} FROM authn.session WHERE handle_hash = :h"),
            {"h": hashlib.sha256(handle.encode()).digest()},
        )
    ).first()
    return _session(row) if row else None


async def list_sessions(conn: AsyncConnection, principal_id: str, now: datetime) -> list[Session]:
    rows = (
        await conn.execute(
            text(
                f"SELECT {_COLS} FROM authn.session WHERE principal_id = :p "
                "AND status IN ('ACTIVE', 'REAUTH_REQUIRED') AND expires_at > :now "
                "ORDER BY auth_time DESC LIMIT 100"
            ),
            {"p": uuid.UUID(principal_id), "now": now},
        )
    ).all()
    return [_session(r) for r in rows]


async def last_sign_in(conn: AsyncConnection, principal_ids: list[str]) -> dict[str, datetime]:
    """Per principal: the auth_time of the most recent identity session (any status)."""
    if not principal_ids:
        return {}
    rows = (
        await conn.execute(
            text(
                "SELECT principal_id, max(auth_time) AS at FROM authn.session "
                "WHERE principal_id = ANY(:p) GROUP BY principal_id"
            ),
            {"p": [uuid.UUID(x) for x in principal_ids]},
        )
    ).all()
    return {str(r.principal_id): r.at for r in rows}


async def revoke_tenant_sessions(conn: AsyncConnection, *, tenant_id: str, reason: str) -> int:
    """Every live session of ONE tenant (tenant offboarding). The tenant is named, never inferred from the connection: the worker that runs this
    bypasses row-level security, and without the filter it would sign every company out."""
    rows = (
        await conn.execute(
            text(
                "SELECT session_id FROM authn.session WHERE tenant_id = CAST(:t AS uuid) "
                "AND status IN ('ACTIVE', 'REAUTH_REQUIRED')"
            ),
            {"t": tenant_id},
        )
    ).all()
    for r in rows:
        await set_status(conn, str(r.session_id), SessionStatus.REVOKED, reason=reason)
    return len(rows)
