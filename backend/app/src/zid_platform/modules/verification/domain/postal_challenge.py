"""WF-05 postal confirmation code (ZID-DTV-WF-05 A08, QA-028; CR-115). Pure domain, no I/O.

A one-time code posted to the claimed address. It shows access to mail at that address during the challenge: never ownership of the property and never
who the person is (A08). It records ADDRESS_ASSOCIATION_CONFIRMED only from the ADDRESS_POSTAL_CHALLENGE method.

- 8 characters from an alphabet without look-alikes; kept only as a keyed hash over the challenge id and the code;
- the expiry is the market's (config), between 7 and 60 days; at most 5 attempts; used once; bound to the address revision it was posted to: a changed
  address revokes it, an expired or used code cannot be replayed (QA-028).
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum

ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O, 1/I
LENGTH = 8
MAX_ATTEMPTS = 5
MIN_TTL, MAX_TTL = timedelta(days=7), timedelta(days=60)


class PostalError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class State(StrEnum):
    PENDING_DISPATCH = "PENDING_DISPATCH"
    DISPATCHED = "DISPATCHED"
    VERIFIED = "VERIFIED"
    EXPIRED = "EXPIRED"
    REVOKED = "REVOKED"


FINAL = frozenset({State.VERIFIED, State.EXPIRED, State.REVOKED})


@dataclass(frozen=True, slots=True)
class Postal:
    challenge_id: str
    address_revision: int
    secret_hash: bytes
    state: State
    expires_at: datetime
    attempt_count: int
    created_at: datetime
    consumed_at: datetime | None = None


def _hash(key: bytes, challenge_id: str, code: str) -> bytes:
    if not key:
        raise PostalError("KEY_REQUIRED")
    return hmac.new(key, f"{challenge_id}|{code}".encode(), hashlib.sha256).digest()


def issue(
    *, challenge_id: str, address_revision: int, key: bytes, ttl: timedelta, now: datetime
) -> tuple[Postal, str]:
    if not MIN_TTL <= ttl <= MAX_TTL:
        raise PostalError("TTL_INVALID")
    code = "".join(secrets.choice(ALPHABET) for _ in range(LENGTH))
    return Postal(
        challenge_id,
        address_revision,
        _hash(key, challenge_id, code),
        State.PENDING_DISPATCH,
        now + ttl,
        0,
        now,
    ), code


def dispatched(p: Postal) -> Postal:
    if p.state is not State.PENDING_DISPATCH:
        raise PostalError("NOT_PENDING_DISPATCH")
    return replace(p, state=State.DISPATCHED)


def confirm(
    p: Postal, code: str, *, key: bytes, address_revision: int, now: datetime
) -> tuple[Postal, bool]:
    """(the challenge after this attempt, verified). The answer is the same for every wrong code."""
    if p.state in FINAL:
        return p, False
    if now >= p.expires_at:
        return replace(p, state=State.EXPIRED), False
    if address_revision != p.address_revision:
        return replace(p, state=State.REVOKED), False
    attempted = replace(p, attempt_count=p.attempt_count + 1)
    normalized = (code or "").strip().upper().replace(" ", "").replace("-", "")
    if re.fullmatch(f"[{ALPHABET}]{{{LENGTH}}}", normalized) and hmac.compare_digest(
        _hash(key, p.challenge_id, normalized), p.secret_hash
    ):
        return replace(attempted, state=State.VERIFIED, consumed_at=now), True
    if attempted.attempt_count >= MAX_ATTEMPTS:
        return replace(attempted, state=State.REVOKED), False
    return attempted, False


__all__ = [
    "ALPHABET",
    "FINAL",
    "LENGTH",
    "MAX_ATTEMPTS",
    "Postal",
    "PostalError",
    "State",
    "confirm",
    "dispatched",
    "issue",
]
