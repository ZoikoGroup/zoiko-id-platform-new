"""Authenticator lifecycle and passkey assurance (ZID-AUTH-001 §3.3, §4, §4.1; ZID-DATA-001 §5.1).

Pure domain, no I/O.
- Status machine §4.1: terminal states (REVOKED, LOST, REPLACED) never come back; a replacement is
  a new record. The database enforces the same transitions.
- A passkey assertion with user verification is phishing-resistant (AAL-Z2); user presence alone
  is Z1. A signCount that fails to advance is a clone/risk signal: the login is capped at Z1
  (forcing step-up for anything that needs Z2) rather than hard-locked (§3.3 "counter anomalies").
- The last active phishing-resistant authenticator cannot be removed outside recovery (§3.3).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

CHALLENGE_TTL = timedelta(minutes=2)  # §6.4: <= 5 minutes, preferably shorter
ENROLLMENT_TICKET_TTL = timedelta(hours=24)
STRONG_TYPES = frozenset({"PASSKEY", "SECURITY_KEY"})


class AuthenticatorStatus(StrEnum):
    PENDING_REGISTRATION = "PENDING_REGISTRATION"
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    REVOKED = "REVOKED"
    LOST = "LOST"
    REPLACED = "REPLACED"


_TRANSITIONS = {
    AuthenticatorStatus.PENDING_REGISTRATION: frozenset(
        {AuthenticatorStatus.ACTIVE, AuthenticatorStatus.REVOKED}
    ),
    AuthenticatorStatus.ACTIVE: frozenset(
        {
            AuthenticatorStatus.SUSPENDED,
            AuthenticatorStatus.REVOKED,
            AuthenticatorStatus.LOST,
            AuthenticatorStatus.REPLACED,
        }
    ),
    AuthenticatorStatus.SUSPENDED: frozenset(
        {AuthenticatorStatus.REVOKED, AuthenticatorStatus.LOST, AuthenticatorStatus.REPLACED}
    ),
}


class AuthnError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


def transition(current: AuthenticatorStatus, target: AuthenticatorStatus) -> AuthenticatorStatus:
    if target not in _TRANSITIONS.get(current, frozenset()):
        raise AuthnError("AUTHENTICATOR_INVALID_TRANSITION", f"{current} -> {target}")
    return target


@dataclass(frozen=True, slots=True)
class Authenticator:
    authenticator_id: str
    tenant_id: str
    principal_id: str
    authenticator_type: str
    status: AuthenticatorStatus
    registered_at: datetime
    credential_id: bytes | None = None
    public_key_cose: bytes | None = None
    sign_count: int = 0
    user_verified: bool = False
    backup_eligible: bool = False
    backed_up: bool = False
    transports: tuple[str, ...] = ()
    label: str | None = None
    aaguid: str | None = None
    last_used_at: datetime | None = None


def sign_count_anomaly(stored: int, presented: int) -> bool:
    """WebAuthn §6.1.1: a counter that is in use must strictly increase. 0/0 means no counter."""
    if stored == 0 and presented == 0:
        return False
    return presented <= stored


@dataclass(frozen=True, slots=True)
class PasskeyAssurance:
    aal: int
    amr: tuple[str, ...]
    anomaly: bool


def passkey_assurance(
    *, user_verified: bool, backup_eligible: bool, anomaly: bool
) -> PasskeyAssurance:
    """amr (RFC 8176): hwk for device-bound keys, swk for synced passkeys; `user` when verified."""
    amr = ("swk" if backup_eligible else "hwk",) + (("user",) if user_verified else ())
    aal = 2 if user_verified and not anomaly else 1
    return PasskeyAssurance(aal, amr, anomaly)


def can_remove(target: Authenticator, others_active_strong: int) -> None:
    """§3.3: removing the last strong factor goes through recovery, not a self-service delete."""
    if target.status not in (AuthenticatorStatus.ACTIVE, AuthenticatorStatus.SUSPENDED):
        raise AuthnError("AUTHENTICATOR_NOT_ACTIVE")
    if target.authenticator_type in STRONG_TYPES and others_active_strong == 0:
        raise AuthnError("LAST_STRONG_AUTHENTICATOR", "use account recovery to replace it")


# WebAuthn's `allowCredentials` may name at most 64 credentials in the major browsers; a longer list makes the browser
# refuse the whole ceremony ("The `allowCredentials` attribute exceeds the maximum allowed size (64)").
MAX_ALLOWED_CREDENTIALS = 64


def credential_allow_list(
    authenticators: list[Authenticator], limit: int = MAX_ALLOWED_CREDENTIALS
) -> list[bytes]:
    """Credential ids a step-up may be answered with: the strong ones, the most recently used or registered first,
    at most `limit` so the browser accepts the request even for a person who holds a very long list of passkeys."""
    strong = [a for a in authenticators if a.credential_id and a.authenticator_type in STRONG_TYPES]
    strong.sort(key=lambda a: a.last_used_at or a.registered_at, reverse=True)
    return [a.credential_id for a in strong[:limit] if a.credential_id]
