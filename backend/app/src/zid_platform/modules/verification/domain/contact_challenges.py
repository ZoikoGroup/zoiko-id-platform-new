"""WF-05 contact claims and one-time challenges (ZID-DTV-WF-05 DTV-20, DTV-21, §06, §22, A02-A04; CR-114). Pure domain, no I/O.

Contacts:
- a phone number is parsed with libphonenumber to E.164 (`infrastructure/phone_numbers.py`; the domain stays free of libraries) for its region. A number that is possible but not known to be valid is accepted and marked
  UNUSUAL (QA-002: review, never a blanket rejection). "Format accepted" never means allocated, reachable or belonging to the person (A02);
- an email address keeps its local part exactly as typed (no provider-specific dot or plus rules, A04); only the domain is lower-cased and IDNA-encoded;
- the UI only ever sees a masked form.

Challenges (A03, A04, §22):
- by phone a random 6-digit code (A03); by email a high-entropy single-use secret for a link (A04: 32 random bytes). Either is kept only as a keyed
  hash over the challenge id and the secret; it is never stored, logged or returned;
- valid for at most 5 minutes; at most 5 wrong attempts; at most 3 sends, 60 seconds apart; a resend gives a new code but never a later expiry;
- bound to the contact's revision and the session that asked: a changed contact revokes it (QA-003, QA-014), another session cannot use it (QA-016);
- used once: VERIFIED is final (QA-004); EXPIRED and REVOKED are final.
A confirmed challenge proves only CONTACT_CONTROL_CONFIRMED at that moment: never identity, never the SIM holder.

Send limits (§22 OTP pumping) are checked before anything is sent: per contact, per case, per phone prefix, and a daily send cap per tenant and channel;
SMS and voice only to an allowed calling code. A limit gives the same answer whatever the number, so it says nothing about an account (QA-009).
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

TTL = timedelta(minutes=5)
MAX_ATTEMPTS = 5
MAX_SENDS = 3
RESEND_GAP = timedelta(seconds=60)
DIGITS = 6
_EMAIL = re.compile(r"^(?P<local>[^@\s<>,;:\"\\]{1,64})@(?P<domain>[^@\s<>,;:\"\\]{1,253})$")
_DOMAIN_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


class ChallengeError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


class ContactKind(StrEnum):
    EMAIL = "EMAIL"
    PHONE = "PHONE"


class Channel(StrEnum):
    EMAIL = "EMAIL"
    SMS = "SMS"
    VOICE = "VOICE"


class State(StrEnum):
    PENDING = "PENDING"
    SENT = "SENT"
    DELIVERED_UNKNOWN = "DELIVERED_UNKNOWN"
    VERIFIED = "VERIFIED"
    EXPIRED = "EXPIRED"
    REVOKED = "REVOKED"


FINAL = frozenset({State.VERIFIED, State.EXPIRED, State.REVOKED})


# ---------------------------------------------------------------------------------------------------------------------- contacts


@dataclass(frozen=True, slots=True)
class Normalized:
    kind: ContactKind
    value: str  # E.164, or local@domain with the domain normalized
    masked: str
    region: str | None = None  # phone: the number's region
    calling_code: str | None = None  # phone: e.g. "44"
    unusual: bool = False  # phone: possible, but not known to be a valid allocated range


def normalize_email(raw: str) -> Normalized:
    text = raw.strip()
    m = _EMAIL.match(text)
    if not m or len(text) > 254 or any(ord(c) < 33 or ord(c) == 127 for c in text):
        raise ChallengeError("EMAIL_INVALID")
    try:
        domain = m["domain"].rstrip(".").encode("idna").decode("ascii").lower()
    except UnicodeError as e:
        raise ChallengeError("EMAIL_INVALID", "domain") from e
    labels = domain.split(".")
    if len(labels) < 2 or not all(_DOMAIN_LABEL.match(x) for x in labels):
        raise ChallengeError("EMAIL_INVALID", "domain")
    value = f"{m['local']}@{domain}"  # the local part is kept exactly as typed
    return Normalized(ContactKind.EMAIL, value, mask_email(value))


def mask_phone(e164: str) -> str:
    return f"{e164[:3]}{'*' * max(0, len(e164) - 7)}{e164[-4:]}"


def mask_email(value: str) -> str:
    local, domain = value.split("@", 1)
    return f"{local[0]}***@{domain}"


def contact_hash(key: bytes, tenant_id: str, kind: ContactKind, value: str) -> str:
    """Keyed and tenant-scoped: the same address in two tenants gives two unrelated values (QA-015)."""
    if not key:
        raise ChallengeError("KEY_REQUIRED")
    return hmac.new(key, f"{tenant_id}|{kind.value}|{value}".encode(), hashlib.sha256).hexdigest()


# ---------------------------------------------------------------------------------------------------------------------- challenges


@dataclass(frozen=True, slots=True)
class Challenge:
    challenge_id: str
    contact_id: str
    contact_revision: int
    channel: Channel
    session_ref: str
    secret_hash: bytes
    state: State
    expires_at: datetime
    attempt_count: int
    send_count: int
    last_sent_at: datetime
    created_at: datetime
    consumed_at: datetime | None = None
    end_reason: str | None = None


def new_code(channel: Channel) -> str:
    if channel is Channel.EMAIL:
        return secrets.token_urlsafe(32)  # 43 characters: a link secret, never something to type
    return f"{secrets.randbelow(10**DIGITS):0{DIGITS}d}"


def well_formed(channel: Channel, code: str) -> bool:
    pattern = r"[A-Za-z0-9_-]{43}" if channel is Channel.EMAIL else rf"\d{{{DIGITS}}}"
    return bool(re.fullmatch(pattern, code or ""))


def secret_hash(key: bytes, challenge_id: str, code: str) -> bytes:
    if not key:
        raise ChallengeError("KEY_REQUIRED")
    return hmac.new(key, f"{challenge_id}|{code}".encode(), hashlib.sha256).digest()


def issue(
    *,
    challenge_id: str,
    contact_id: str,
    contact_revision: int,
    channel: Channel,
    session_ref: str,
    key: bytes,
    now: datetime,
    ttl: timedelta = TTL,
) -> tuple[Challenge, str]:
    """A new challenge and its code. The code leaves only toward the person; the caller keeps the challenge."""
    if not session_ref.strip():
        raise ChallengeError("SESSION_REQUIRED")
    if not timedelta(seconds=30) <= ttl <= TTL:
        raise ChallengeError("TTL_INVALID", "at most 5 minutes")
    code = new_code(channel)
    ch = Challenge(
        challenge_id=challenge_id, contact_id=contact_id, contact_revision=contact_revision, channel=channel,
        session_ref=session_ref, secret_hash=secret_hash(key, challenge_id, code), state=State.SENT, expires_at=now + ttl,
        attempt_count=0, send_count=1, last_sent_at=now, created_at=now,
    )  # fmt: skip
    return ch, code


def resend(ch: Challenge, *, key: bytes, now: datetime) -> tuple[Challenge, str]:
    """A new code for the same challenge: never a later expiry, at most MAX_SENDS, RESEND_GAP apart."""
    if ch.state in FINAL:
        raise ChallengeError("CHALLENGE_FINAL", ch.state.value)
    if now >= ch.expires_at:
        raise ChallengeError("CHALLENGE_EXPIRED")
    if ch.send_count >= MAX_SENDS:
        raise ChallengeError("CHALLENGE_RATE_LIMITED", "no more sends for this challenge")
    if now - ch.last_sent_at < RESEND_GAP:
        raise ChallengeError("CHALLENGE_RATE_LIMITED", "wait before asking again")
    code = new_code(ch.channel)
    return replace(
        ch, secret_hash=secret_hash(key, ch.challenge_id, code), send_count=ch.send_count + 1, last_sent_at=now
    ), code  # fmt: skip


@dataclass(frozen=True, slots=True)
class Outcome:
    challenge: Challenge
    verified: bool
    reason: str | None  # why not, a stable code; the same for every wrong code


def confirm(
    ch: Challenge,
    code: str,
    *,
    key: bytes,
    contact_revision: int,
    session_ref: str,
    now: datetime,
) -> Outcome:
    """Check a code once. Every attempt counts; the contact's revision and the asking session must match; VERIFIED is final."""
    if ch.state in FINAL:
        return Outcome(ch, False, f"CHALLENGE_{ch.state.value}")
    if now >= ch.expires_at:
        return Outcome(
            replace(ch, state=State.EXPIRED, end_reason="EXPIRED"), False, "CHALLENGE_EXPIRED"
        )
    if contact_revision != ch.contact_revision:
        return Outcome(
            replace(ch, state=State.REVOKED, end_reason="CONTACT_CHANGED"),
            False,
            "CHALLENGE_REVOKED",
        )
    attempted = replace(ch, attempt_count=ch.attempt_count + 1)
    good = (
        session_ref == ch.session_ref
        and well_formed(ch.channel, code)
        and hmac.compare_digest(secret_hash(key, ch.challenge_id, code), ch.secret_hash)
    )
    if good:
        return Outcome(replace(attempted, state=State.VERIFIED, consumed_at=now), True, None)
    if attempted.attempt_count >= MAX_ATTEMPTS:
        return Outcome(
            replace(attempted, state=State.REVOKED, end_reason="ATTEMPTS_EXHAUSTED"),
            False,
            "CHALLENGE_REVOKED",
        )
    return Outcome(attempted, False, "CHALLENGE_INVALID")


def revoke(ch: Challenge, reason: str) -> Challenge:
    return ch if ch.state in FINAL else replace(ch, state=State.REVOKED, end_reason=reason)


# ---------------------------------------------------------------------------------------------------------------------- send limits


@dataclass(frozen=True, slots=True)
class Limits:
    per_contact_hour: int
    per_case_hour: int
    per_prefix_hour: int
    daily_cap: Mapping[Channel, int]  # per tenant and channel; 0 means no sends on that channel
    phone_calling_codes: frozenset[str]  # SMS and voice only to these; empty means none
    status: str  # SAMPLE | APPROVED


@dataclass(frozen=True, slots=True)
class Recent:
    contact_hour: int
    case_hour: int
    prefix_hour: int
    tenant_day: int


def parse_limits(doc: Mapping[str, Any]) -> Limits:
    def count(name: str, body: Mapping[str, Any]) -> int:
        v = body.get(name)
        if not isinstance(v, int) or isinstance(v, bool) or not 0 <= v <= 1_000_000:
            raise ChallengeError("LIMITS_INVALID", name)
        return v

    status = str(doc.get("status", "SAMPLE"))
    if status not in {"SAMPLE", "APPROVED"}:
        raise ChallengeError("LIMITS_INVALID", "status")
    caps = doc.get("daily_cap") or {}
    codes = doc.get("phone_calling_codes") or []
    if not all(re.fullmatch(r"[1-9]\d{0,2}", str(c)) for c in codes):
        raise ChallengeError("LIMITS_INVALID", "phone_calling_codes")
    return Limits(
        count("per_contact_hour", doc), count("per_case_hour", doc), count("per_prefix_hour", doc),
        {ch: count(ch.value, caps) for ch in Channel}, frozenset(str(c) for c in codes), status,
    )  # fmt: skip


def check_send(
    limits: Limits, channel: Channel, recent: Recent, *, calling_code: str | None
) -> None:
    """Before any send. One stable code for every limit, so a limit says nothing about whether a contact or account exists."""
    if channel is not Channel.EMAIL and (calling_code not in limits.phone_calling_codes):
        raise ChallengeError("CHANNEL_NOT_ALLOWED_FOR_DESTINATION")
    if (
        recent.tenant_day >= limits.daily_cap[channel]
        or recent.contact_hour >= limits.per_contact_hour
        or recent.case_hour >= limits.per_case_hour
        or (channel is not Channel.EMAIL and recent.prefix_hour >= limits.per_prefix_hour)
    ):
        raise ChallengeError("CHALLENGE_RATE_LIMITED")


__all__ = [
    "FINAL", "MAX_ATTEMPTS", "MAX_SENDS", "RESEND_GAP", "TTL", "Challenge", "ChallengeError", "Channel", "ContactKind", "Limits",
    "Normalized", "Outcome", "Recent", "State", "check_send", "confirm", "contact_hash", "issue", "mask_email", "mask_phone",
    "new_code", "normalize_email", "parse_limits", "resend", "revoke", "secret_hash", "well_formed",
]  # fmt: skip
