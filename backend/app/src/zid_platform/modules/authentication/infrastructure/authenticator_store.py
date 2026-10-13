"""Authenticators, WebAuthn challenges and enrollment tickets in PostgreSQL (ZID-DATA-001 §5.1).

Runs inside the caller's tenant transaction (RLS). Challenges and tickets are stored as SHA-256 and
claimed with a conditional UPDATE, so each is usable exactly once even under concurrent requests.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection
from zid_outbox import emit

from zid_platform.modules.authentication.domain.authenticator import (
    Authenticator,
    AuthenticatorStatus,
    AuthnError,
    transition,
)

_COLS = (
    "authenticator_id, tenant_id, principal_id, authenticator_type, status, registered_at, "
    "credential_id, public_key_cose, sign_count, user_verified, backup_eligible, backed_up, "
    "transports, label, aaguid, last_used_at"
)


def sha256(data: bytes | str) -> bytes:
    return hashlib.sha256(data.encode() if isinstance(data, str) else data).digest()


def _row(r: object) -> Authenticator:
    m = r._mapping  # type: ignore[attr-defined]
    return Authenticator(
        authenticator_id=str(m["authenticator_id"]),
        tenant_id=str(m["tenant_id"]),
        principal_id=str(m["principal_id"]),
        authenticator_type=m["authenticator_type"],
        status=AuthenticatorStatus(m["status"]),
        registered_at=m["registered_at"],
        credential_id=bytes(m["credential_id"]) if m["credential_id"] is not None else None,
        public_key_cose=bytes(m["public_key_cose"]) if m["public_key_cose"] is not None else None,
        sign_count=m["sign_count"],
        user_verified=m["user_verified"],
        backup_eligible=m["backup_eligible"],
        backed_up=m["backed_up"],
        transports=tuple(m["transports"]),
        label=m["label"],
        aaguid=str(m["aaguid"]) if m["aaguid"] else None,
        last_used_at=m["last_used_at"],
    )


async def add_authenticator(conn: AsyncConnection, a: Authenticator, *, aal: int) -> None:
    try:
        async with conn.begin_nested():
            await conn.execute(
                text(
                    "INSERT INTO authn.authenticator (authenticator_id, tenant_id, principal_id, "
                    "authenticator_type, status, registered_at, credential_id, credential_id_hash, "
                    "public_key_cose, sign_count, user_verified, backup_eligible, backed_up, "
                    "transports, label, aaguid, assurance_level) VALUES (:id, :t, :p, :ty, "
                    "CAST(:st AS authn.authenticator_status), :at, :cid, :cidh, :pk, :sc, :uv, :be, "
                    ":bu, :tr, :label, :aaguid, :aal)"
                ),
                {
                    "id": uuid.UUID(a.authenticator_id),
                    "t": uuid.UUID(a.tenant_id),
                    "p": uuid.UUID(a.principal_id),
                    "ty": a.authenticator_type,
                    "st": a.status.value,
                    "at": a.registered_at,
                    "cid": a.credential_id,
                    "cidh": sha256(a.credential_id) if a.credential_id else None,
                    "pk": a.public_key_cose,
                    "sc": a.sign_count,
                    "uv": a.user_verified,
                    "be": a.backup_eligible,
                    "bu": a.backed_up,
                    "tr": list(a.transports),
                    "label": a.label,
                    "aaguid": uuid.UUID(a.aaguid) if a.aaguid else None,
                    "aal": f"AAL-Z{aal}",
                },
            )
    except IntegrityError as e:
        raise AuthnError("CREDENTIAL_ALREADY_REGISTERED") from e
    await emit(
        conn,
        name="authenticator.registered",
        tenant_id=a.tenant_id,
        aggregate_type="authenticator",
        aggregate_id=a.authenticator_id,
        aggregate_version=1,
        data={
            "authenticator_id": a.authenticator_id,
            "principal_id": a.principal_id,
            "authenticator_type": a.authenticator_type,
            "backup_eligible": a.backup_eligible,
        },
    )


async def by_credential(conn: AsyncConnection, credential_id: bytes) -> Authenticator | None:
    row = (
        await conn.execute(
            text(f"SELECT {_COLS} FROM authn.authenticator WHERE credential_id_hash = :h"),
            {"h": sha256(credential_id)},
        )
    ).first()
    return _row(row) if row else None


async def load(conn: AsyncConnection, authenticator_id: str) -> Authenticator | None:
    try:
        aid = uuid.UUID(authenticator_id)
    except ValueError:
        return None
    row = (
        await conn.execute(
            text(f"SELECT {_COLS} FROM authn.authenticator WHERE authenticator_id = :a FOR UPDATE"),
            {"a": aid},
        )
    ).first()
    return _row(row) if row else None


async def for_principal(
    conn: AsyncConnection, principal_id: str, *, active_only: bool = False
) -> list[Authenticator]:
    where = " AND status = 'ACTIVE'" if active_only else ""
    rows = (
        await conn.execute(
            text(
                f"SELECT {_COLS} FROM authn.authenticator WHERE principal_id = :p{where} "
                "ORDER BY registered_at"
            ),
            {"p": uuid.UUID(principal_id)},
        )
    ).all()
    return [_row(r) for r in rows]


async def record_use(
    conn: AsyncConnection, a: Authenticator, *, sign_count: int, backed_up: bool, now: datetime
) -> None:
    await conn.execute(
        text(
            "UPDATE authn.authenticator SET sign_count = greatest(sign_count, :sc), "
            "backed_up = :bu, last_used_at = :now WHERE authenticator_id = :a"
        ),
        {"a": uuid.UUID(a.authenticator_id), "sc": sign_count, "bu": backed_up, "now": now},
    )


async def set_status(
    conn: AsyncConnection,
    a: Authenticator,
    target: AuthenticatorStatus,
    *,
    reason: str,
    now: datetime,
) -> None:
    transition(a.status, target)
    result = await conn.execute(
        text(
            "UPDATE authn.authenticator SET status = CAST(:st AS authn.authenticator_status), "
            "revoked_at = CASE WHEN :st IN ('REVOKED', 'LOST', 'REPLACED') THEN :now "
            "ELSE revoked_at END, revoked_reason = :r "
            "WHERE authenticator_id = :a AND status = CAST(:old AS authn.authenticator_status)"
        ),
        {
            "st": target.value,
            "old": a.status.value,
            "a": uuid.UUID(a.authenticator_id),
            "r": reason,
            "now": now,
        },
    )
    if result.rowcount != 1:
        raise AuthnError("CONCURRENT_MODIFICATION")
    if target in (
        AuthenticatorStatus.REVOKED,
        AuthenticatorStatus.LOST,
        AuthenticatorStatus.REPLACED,
        AuthenticatorStatus.SUSPENDED,
    ):
        await emit(
            conn,
            name="authenticator.revoked",
            tenant_id=a.tenant_id,
            aggregate_type="authenticator",
            aggregate_id=a.authenticator_id,
            aggregate_version=3 if a.status is AuthenticatorStatus.SUSPENDED else 2,  # contiguous
            data={
                "authenticator_id": a.authenticator_id,
                "principal_id": a.principal_id,
                "status": target.value,
                "reason": reason,
            },
        )


@dataclass(frozen=True, slots=True)
class Challenge:
    ceremony: str
    principal_id: str | None
    session_id: str | None
    ticket_hash: bytes | None
    user_verification: str
    expires_at: datetime


async def save_challenge(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    challenge: bytes,
    ceremony: str,
    principal_id: str | None,
    session_id: str | None,
    ticket_hash: bytes | None,
    user_verification: str,
    expires_at: datetime,
) -> None:
    await conn.execute(
        text(
            "INSERT INTO authn.webauthn_challenge (challenge_hash, tenant_id, ceremony, principal_id, "
            "session_id, ticket_hash, user_verification, expires_at) "
            "VALUES (:h, :t, :c, :p, :s, :tk, :uv, :exp)"
        ),
        {
            "h": sha256(challenge),
            "t": uuid.UUID(tenant_id),
            "c": ceremony,
            "p": uuid.UUID(principal_id) if principal_id else None,
            "s": uuid.UUID(session_id) if session_id else None,
            "tk": ticket_hash,
            "uv": user_verification,
            "exp": expires_at,
        },
    )


async def claim_challenge(
    conn: AsyncConnection, challenge: bytes, *, ceremonies: tuple[str, ...], now: datetime
) -> Challenge | None:
    """Single use: the first claim inside the TTL wins; replays and expired challenges get None."""
    row = (
        await conn.execute(
            text(
                "UPDATE authn.webauthn_challenge SET used_at = :now WHERE challenge_hash = :h "
                "AND used_at IS NULL AND expires_at > :now AND ceremony = ANY(:c) "
                "RETURNING ceremony, principal_id, session_id, ticket_hash, user_verification, "
                "expires_at"
            ),
            {"h": sha256(challenge), "now": now, "c": list(ceremonies)},
        )
    ).first()
    if row is None:
        return None
    return Challenge(
        ceremony=row.ceremony,
        principal_id=str(row.principal_id) if row.principal_id else None,
        session_id=str(row.session_id) if row.session_id else None,
        ticket_hash=bytes(row.ticket_hash) if row.ticket_hash else None,
        user_verification=row.user_verification,
        expires_at=row.expires_at,
    )


@dataclass(frozen=True, slots=True)
class Ticket:
    ticket_hash: bytes
    principal_id: str
    purpose: str
    recovery_id: str | None


async def issue_ticket(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    principal_id: str,
    purpose: str,
    issued_by: str,
    expires_at: datetime,
    recovery_id: str | None = None,
) -> str:
    ticket = secrets.token_urlsafe(32)
    await conn.execute(
        text(
            "INSERT INTO authn.enrollment_ticket (ticket_hash, tenant_id, principal_id, purpose, "
            "issued_by, recovery_id, expires_at) VALUES (:h, :t, :p, :pu, :by, :r, :exp)"
        ),
        {
            "h": sha256(ticket),
            "t": uuid.UUID(tenant_id),
            "p": uuid.UUID(principal_id),
            "pu": purpose,
            "by": issued_by,
            "r": uuid.UUID(recovery_id) if recovery_id else None,
            "exp": expires_at,
        },
    )
    return ticket


async def peek_ticket(conn: AsyncConnection, ticket: str, now: datetime) -> Ticket | None:
    """A live ticket (unused, unexpired), without spending it."""
    row = (
        await conn.execute(
            text(
                "SELECT ticket_hash, principal_id, purpose, recovery_id FROM authn.enrollment_ticket "
                "WHERE ticket_hash = :h AND used_at IS NULL AND expires_at > :now"
            ),
            {"h": sha256(ticket), "now": now},
        )
    ).first()
    if row is None:
        return None
    return Ticket(
        bytes(row.ticket_hash),
        str(row.principal_id),
        row.purpose,
        str(row.recovery_id) if row.recovery_id else None,
    )


async def spend_ticket(conn: AsyncConnection, ticket_hash: bytes, now: datetime) -> Ticket | None:
    row = (
        await conn.execute(
            text(
                "UPDATE authn.enrollment_ticket SET used_at = :now WHERE ticket_hash = :h "
                "AND used_at IS NULL AND expires_at > :now "
                "RETURNING ticket_hash, principal_id, purpose, recovery_id"
            ),
            {"h": ticket_hash, "now": now},
        )
    ).first()
    if row is None:
        return None
    return Ticket(
        bytes(row.ticket_hash),
        str(row.principal_id),
        row.purpose,
        str(row.recovery_id) if row.recovery_id else None,
    )
