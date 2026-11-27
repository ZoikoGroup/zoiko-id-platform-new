"""The hosted applicant's credentials and journey marks on a verification session (ZID-CP-WF01 sections 05, 08, S10, S12).

The person never signs in. The business's server opens the session and receives a one-use LAUNCH token; the person's browser exchanges it for an ACCESS token
bound to that one session. A resume link and a cross-device handoff are new LAUNCH tokens. Only a SHA-256 of each token is stored, a used or expired token is
refused, and a resume revokes every token issued before it, so a forwarded or intercepted link cannot take the session over.

A token carries the tenant it belongs to in clear (an opaque id, never personal data) so the lookup can run inside that tenant's row-level security.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.privacy.domain.model import PrivacyError
from zid_platform.modules.privacy.infrastructure import store

# Session and link lifetimes are the owners' decision (ZID-EXT-IDV-001 D09): set from settings by the composition root; conservative defaults until then.
LAUNCH_TTL = timedelta(minutes=15)
HANDOFF_TTL = timedelta(minutes=5)
ACCESS_TTL = timedelta(minutes=60)
SESSION_TTL = timedelta(hours=24)


def configure_lifetimes(
    *, launch: timedelta, handoff: timedelta, access: timedelta, session: timedelta
) -> None:
    global LAUNCH_TTL, HANDOFF_TTL, ACCESS_TTL, SESSION_TTL
    LAUNCH_TTL, HANDOFF_TTL, ACCESS_TTL, SESSION_TTL = launch, handoff, access, session


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def tenant_of(token: str) -> str:
    """The tenant a token belongs to, or PrivacyError TOKEN_INVALID when it is not one of ours."""
    head, _, secret = token.partition(".")
    try:
        if len(secret) < 32:
            raise ValueError
        return str(uuid.UUID(hex=head))
    except ValueError as e:
        raise PrivacyError("TOKEN_INVALID", "the link is not valid") from e


async def _issue(
    conn: AsyncConnection, tenant_id: str, ref: str, kind: str, ttl: timedelta, now: datetime
) -> dict[str, Any]:
    token = f"{uuid.UUID(tenant_id).hex}.{secrets.token_urlsafe(32)}"
    expires = now + ttl
    await conn.execute(
        text(
            "INSERT INTO privacy.applicant_token (token_hash, tenant_id, session_ref, kind, expires_at, created_at) VALUES (:h, :t, :r, :k, :e, :now)"
        ),
        {
            "h": _hash(token),
            "t": uuid.UUID(tenant_id),
            "r": ref,
            "k": kind,
            "e": expires,
            "now": now,
        },
    )
    return {"token": token, "expires_at": expires.isoformat()}


async def _session(conn: AsyncConnection, ref: str, *, lock: bool = False) -> Any:
    row = (
        await conn.execute(
            text(
                "SELECT * FROM privacy.verification_session WHERE session_ref = :r"
                + (" FOR UPDATE" if lock else "")
            ),
            {"r": ref},
        )
    ).first()
    if row is None:
        raise PrivacyError("NOT_FOUND", "unknown verification session")
    return row


def _usable(s: Any, now: datetime) -> None:
    if s.state == "CANCELED":
        raise PrivacyError("SESSION_CANCELED")
    if s.expires_at is not None and s.expires_at <= now:
        raise PrivacyError("SESSION_EXPIRED", "this verification link has expired")


async def issue_launch(
    conn: AsyncConnection,
    tenant_id: str,
    ref: str,
    now: datetime,
    *,
    handoff: bool = False,
    revoke_earlier: bool = False,
) -> dict[str, Any]:
    """A one-use link for the person. A resume revokes every earlier token first; a handoff to another device does not end the current one."""
    s = await _session(conn, ref, lock=True)
    _usable(s, now)
    if revoke_earlier:
        await conn.execute(
            text(
                "UPDATE privacy.applicant_token SET revoked_at = :now WHERE session_ref = :r AND revoked_at IS NULL"
            ),
            {"r": ref, "now": now},
        )
    return await _issue(conn, tenant_id, ref, "LAUNCH", HANDOFF_TTL if handoff else LAUNCH_TTL, now)


async def exchange(
    conn: AsyncConnection, tenant_id: str, launch_token: str, now: datetime
) -> dict[str, Any]:
    """Spend a LAUNCH token once and return an ACCESS token for its session."""
    row = (
        await conn.execute(
            text(
                "SELECT * FROM privacy.applicant_token WHERE token_hash = :h AND kind = 'LAUNCH' FOR UPDATE"
            ),
            {"h": _hash(launch_token)},
        )
    ).first()
    if row is None or row.revoked_at is not None:
        raise PrivacyError("TOKEN_INVALID", "the link is not valid")
    if row.used_at is not None:
        raise PrivacyError("TOKEN_ALREADY_USED", "the link was already used")
    if row.expires_at <= now:
        raise PrivacyError("SESSION_EXPIRED", "this verification link has expired")
    _usable(await _session(conn, row.session_ref), now)
    await conn.execute(
        text("UPDATE privacy.applicant_token SET used_at = :now WHERE token_hash = :h"),
        {"h": row.token_hash, "now": now},
    )
    access = await _issue(conn, tenant_id, row.session_ref, "ACCESS", ACCESS_TTL, now)
    return {
        "session_ref": row.session_ref,
        "access_token": access["token"],
        "expires_at": access["expires_at"],
    }


async def resolve(conn: AsyncConnection, access_token: str, now: datetime) -> Any:
    """The session an ACCESS token is bound to, while the token and the session are both live."""
    row = (
        await conn.execute(
            text(
                "SELECT session_ref, expires_at, revoked_at FROM privacy.applicant_token WHERE token_hash = :h AND kind = 'ACCESS'"
            ),
            {"h": _hash(access_token)},
        )
    ).first()
    if row is None or row.revoked_at is not None:
        raise PrivacyError("TOKEN_INVALID", "the session credential is not valid")
    if row.expires_at <= now:
        raise PrivacyError("SESSION_EXPIRED", "the session credential has expired")
    return await _session(conn, row.session_ref)


async def session_row(conn: AsyncConnection, ref: str) -> Any:
    return await _session(conn, ref)


async def next_event_version(conn: AsyncConnection, ref: str) -> int:
    """The session's next event version: every event about one session is ordered, and none can collide with another."""
    return int(
        (
            await conn.execute(
                text(
                    "UPDATE privacy.verification_session SET event_seq = event_seq + 1 WHERE session_ref = :r RETURNING event_seq"
                ),
                {"r": ref},
            )
        ).scalar_one()
    )


async def session_for_case(conn: AsyncConnection, case_id: str) -> Any:
    """The hosted session a proofing case belongs to, or None for a case opened another way."""
    return (
        await conn.execute(
            text("SELECT * FROM privacy.verification_session WHERE case_id = :c"),
            {"c": uuid.UUID(case_id)},
        )
    ).first()


async def link_case(conn: AsyncConnection, ref: str, case_id: str) -> None:
    await conn.execute(
        text("UPDATE privacy.verification_session SET case_id = :c WHERE session_ref = :r"),
        {"c": uuid.UUID(case_id), "r": ref},
    )


async def mark_presented(conn: AsyncConnection, ref: str, now: datetime) -> bool:
    """True the first time the notice is presented for this session."""
    got = await conn.execute(
        text(
            "UPDATE privacy.verification_session SET presented_at = :now WHERE session_ref = :r AND presented_at IS NULL"
        ),
        {"r": ref, "now": now},
    )
    return bool(got.rowcount)


async def acknowledge(
    conn: AsyncConnection, ref: str, *, locale: str, biometric_consent: bool, now: datetime
) -> None:
    """The person acknowledged the notice in this language (the first acknowledgment stands) and agreed, or not, to a face comparison (WF-01 S01, Q07, Q08)."""
    await conn.execute(
        text(
            "UPDATE privacy.verification_session SET acknowledged_at = COALESCE(acknowledged_at, :now), notice_locale = COALESCE(notice_locale, :l), "
            "biometric_consent_at = CASE WHEN :b THEN COALESCE(biometric_consent_at, :now) ELSE NULL END WHERE session_ref = :r"
        ),
        {"r": ref, "now": now, "l": locale, "b": biometric_consent},
    )


async def mark_submitted(conn: AsyncConnection, ref: str, now: datetime) -> bool:
    """True only for the first submission: a repeat changes nothing (WF-01 S08 one final, idempotent submission)."""
    got = await conn.execute(
        text(
            "UPDATE privacy.verification_session SET submitted_at = :now WHERE session_ref = :r AND submitted_at IS NULL"
        ),
        {"r": ref, "now": now},
    )
    return bool(got.rowcount)


async def lawful_basis(conn: AsyncConnection, activity_code: str) -> str | None:
    activity = await store.active_activity(conn, activity_code)
    return activity.lawful_basis if activity is not None else None


HostedGate = Callable[[Any, AsyncConnection, str, str | None], Awaitable[str]]
_gate: HostedGate | None = None


def configure_hosted_gate(gate: HostedGate | None) -> None:
    """Wired by the composition root (main): what a hosted session must pass before it opens (the market's regulatory checks and the return address), and
    whether it runs in sandbox or production. Privacy never imports the modules that answer it."""
    global _gate
    _gate = gate


async def hosted_gate(
    request: Any, conn: AsyncConnection, market: str, return_url: str | None
) -> str:
    """The mode ('sandbox' or 'production') once the gate passes; it raises the refusal otherwise. With no gate wired no hosted session opens."""
    if _gate is None:
        raise PrivacyError(
            "HOSTED_SESSIONS_UNAVAILABLE",
            "hosted verification is not configured in this deployment",
        )
    return await _gate(request, conn, market, return_url)


def return_url_allowed(url: str, verified_domains: list[str], *, local: bool) -> bool:
    """WF-01 S10: the person is only ever sent back to an https address on a domain the business has proven it controls (no open redirect)."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.username or parts.password or not host:
        return False
    if local and parts.scheme == "http" and host in ("localhost", "127.0.0.1"):
        return True
    if parts.scheme != "https":
        return False
    return any(host == d.lower() or host.endswith("." + d.lower()) for d in verified_domains)
