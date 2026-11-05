"""Operator Console password check: salted scrypt hashes and a failed-attempt lockout. Pure domain, no I/O.

The Operator Console is the one place that signs in with an email and password; every other sign-in is a passkey. The
stored form is `scrypt$<n>$<r>$<p>$<salt>$<hash>` (URL-safe base64, no padding), produced by `hash_password`.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass, field

_N, _R, _P = 2**14, 8, 1
_KEY_LEN = 32
MIN_LENGTH = 12
MAX_FAILURES = 5
LOCKOUT_SECONDS = 15 * 60


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _derive(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(
        password.encode(), salt=salt, n=n, r=r, p=p, dklen=_KEY_LEN, maxmem=128 * n * r * 2
    )


def hash_password(password: str, *, min_length: int = MIN_LENGTH) -> str:
    if len(password) < min_length:
        raise ValueError(f"the password must be at least {min_length} characters")
    salt = secrets.token_bytes(16)
    return f"scrypt${_N}${_R}${_P}${_b64(salt)}${_b64(_derive(password, salt, _N, _R, _P))}"


def valid_hash(stored: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        return scheme == "scrypt" and int(n) > 1 and int(r) > 0 and int(p) > 0 and bool(_unb64(salt)) and bool(_unb64(digest))
    except ValueError:
        return False


def verify_password(password: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, digest = stored.split("$")
        derived = _derive(password, _unb64(salt), int(n), int(r), int(p))
    except ValueError:
        return False
    return hmac.compare_digest(derived, _unb64(digest))


def operator_principal_id(tenant_id: str, email: str, configured: str | None) -> str:
    """The operator's principal id: the configured one, else a stable uuid derived from the tenant and the email."""
    if configured:
        return str(uuid.UUID(configured))
    return str(uuid.uuid5(uuid.UUID(tenant_id), f"operator:{email.strip().lower()}"))


# A hash nobody knows the password of, checked when the email is unknown so a miss takes as long as a wrong password.
DECOY_HASH = hash_password(secrets.token_urlsafe(24))


@dataclass(slots=True)
class Lockout:
    """Failed attempts per email: MAX_FAILURES in a row lock that email for LOCKOUT_SECONDS. Kept in this process."""

    _failures: dict[str, tuple[int, float]] = field(default_factory=dict)

    def locked_for(self, email: str, now: float) -> int:
        count, until = self._failures.get(email, (0, 0.0))
        return int(until - now) + 1 if count >= MAX_FAILURES and until > now else 0

    def failed(self, email: str, now: float) -> None:
        count, until = self._failures.get(email, (0, 0.0))
        if count >= MAX_FAILURES and until <= now:
            count = 0
        self._failures[email] = (count + 1, now + LOCKOUT_SECONDS)

    def succeeded(self, email: str) -> None:
        self._failures.pop(email, None)
