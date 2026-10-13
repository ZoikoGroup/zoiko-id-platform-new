"""Identity session rules (ZID-AUTH-001 §5, §16; ZID-DATA-001 §5.3). Pure domain, no I/O.

An identity session is Zoiko's record that a principal authenticated under a context and a
security revision. It is never authority. It ends at its absolute expiry, after idle time, on
revocation, and needs re-authentication as soon as the principal's security revision differs from
the one it observed (credential revoked, recovery, suspension...).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum


class SessionStatus(StrEnum):
    ACTIVE = "ACTIVE"
    REAUTH_REQUIRED = "REAUTH_REQUIRED"
    REVOKED = "REVOKED"
    EXPIRED = "EXPIRED"


TERMINAL = frozenset({SessionStatus.REVOKED, SessionStatus.EXPIRED})
_TRANSITIONS = {
    SessionStatus.ACTIVE: frozenset(
        {SessionStatus.REAUTH_REQUIRED, SessionStatus.REVOKED, SessionStatus.EXPIRED}
    ),
    SessionStatus.REAUTH_REQUIRED: frozenset({SessionStatus.REVOKED, SessionStatus.EXPIRED}),
}


class SessionError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


def transition(current: SessionStatus, target: SessionStatus) -> SessionStatus:
    if target not in _TRANSITIONS.get(current, frozenset()):
        raise SessionError("SESSION_INVALID_TRANSITION", f"{current} -> {target}")
    return target


@dataclass(frozen=True, slots=True)
class SessionPolicy:
    absolute_ttl: timedelta = timedelta(hours=12)
    idle_timeout: timedelta = timedelta(minutes=30)

    def __post_init__(self) -> None:
        if not timedelta(0) < self.idle_timeout <= self.absolute_ttl:
            raise ValueError("idle timeout must be positive and within the absolute lifetime")


PRIVILEGED_POLICY = SessionPolicy(
    absolute_ttl=timedelta(hours=4), idle_timeout=timedelta(minutes=10)
)


@dataclass(frozen=True, slots=True)
class Session:
    session_id: str
    tenant_id: str
    principal_id: str
    auth_time: datetime
    authentication_context: str  # e.g. "native:webauthn" or "oidc:<issuer>"
    aal: int  # mapped base level (0-2); Z3 is derived from freshness, never stored
    observed_security_revision: int
    risk_class: int
    status: SessionStatus
    expires_at: datetime
    last_seen_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class SessionCheck:
    ok: bool
    code: str | None = None
    next_status: SessionStatus | None = None  # state the store should persist, if it changes


def check(
    s: Session,
    *,
    principal_revision: int,
    principal_active: bool,
    now: datetime,
    policy: SessionPolicy,
    idle: bool = True,
) -> SessionCheck:
    """`idle=False` for refresh-token redemption: a refresh is the client's own activity, and the
    family is bounded by the session's absolute expiry, revocation and security revision instead."""
    if s.status is SessionStatus.REVOKED:
        return SessionCheck(False, "SESSION_REVOKED")
    if s.status is SessionStatus.EXPIRED:
        return SessionCheck(False, "SESSION_EXPIRED")
    if now >= s.expires_at:
        return SessionCheck(False, "SESSION_EXPIRED", SessionStatus.EXPIRED)
    if not principal_active:
        return SessionCheck(False, "PRINCIPAL_NOT_ACTIVE", SessionStatus.REVOKED)
    if (
        principal_revision != s.observed_security_revision
        or s.status is SessionStatus.REAUTH_REQUIRED
    ):
        # Fail closed in both directions: a lower revision means our view is behind the session's.
        nxt = None if s.status is SessionStatus.REAUTH_REQUIRED else SessionStatus.REAUTH_REQUIRED
        return SessionCheck(False, "SESSION_REAUTH_REQUIRED", nxt)
    if idle and now - (s.last_seen_at or s.auth_time) > policy.idle_timeout:
        return SessionCheck(False, "SESSION_IDLE_TIMEOUT", SessionStatus.EXPIRED)
    return SessionCheck(True)
