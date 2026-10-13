"""Authenticator management (ZID-AUTH-001 §3.3 "Deletion", §4.1, §16; ZID-DATA-001 §5.1).

Removing an authenticator is protected: it needs AAL-Z3 (a strong authentication within the
freshness window), it is refused for the last active strong factor (that path is recovery), and
it increments the principal's security revision, so every session observing the old revision -
including the caller's - must re-authenticate (§16 "Authenticator revoke").
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.authentication.domain.assurance import AAL, effective_level
from zid_platform.modules.authentication.domain.authenticator import (
    STRONG_TYPES,
    AuthenticatorStatus,
    AuthnError,
    can_remove,
)
from zid_platform.modules.authentication.infrastructure import authenticator_store as store
from zid_platform.modules.principal.ports import bump_security_revision


async def revoke_own(
    conn: AsyncConnection,
    *,
    principal_id: str,
    authenticator_id: str,
    aal: int,
    auth_time: datetime,
    now: datetime,
) -> None:
    if effective_level(AAL(min(aal, 2)), auth_time=auth_time, now=now) < AAL.Z3:
        raise AuthnError("STEP_UP_REQUIRED")
    target = await store.load(conn, authenticator_id)
    if target is None or target.principal_id != principal_id:
        raise AuthnError("AUTHENTICATOR_NOT_FOUND")
    others = [
        a
        for a in await store.for_principal(conn, principal_id, active_only=True)
        if a.authenticator_id != target.authenticator_id and a.authenticator_type in STRONG_TYPES
    ]
    can_remove(target, len(others))
    await store.set_status(
        conn, target, AuthenticatorStatus.REVOKED, reason="owner_removed", now=now
    )
    await bump_security_revision(conn, principal_id, reason="authenticator_revoked")


async def revoke_for_security(
    conn: AsyncConnection, *, authenticator_id: str, reason: str, now: datetime
) -> bool:
    """A trusted security signal or recovery revokes an authenticator (§16; SIGNALS §16).

    No step-up or last-factor rule: this path exists precisely when the factor is untrustworthy.
    The security revision increments so sessions it backed must re-authenticate."""
    target = await store.load(conn, authenticator_id)
    if target is None or target.status not in (
        AuthenticatorStatus.ACTIVE,
        AuthenticatorStatus.SUSPENDED,
    ):
        return False
    await store.set_status(conn, target, AuthenticatorStatus.REVOKED, reason=reason, now=now)
    await bump_security_revision(conn, target.principal_id, reason="authenticator_revoked")
    return True
