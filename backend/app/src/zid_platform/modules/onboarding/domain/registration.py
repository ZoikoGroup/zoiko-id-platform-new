"""Person-first sign-up rules (ZID-UX-WF-013A; slice L-11, CR-048). Pure: no database, no clock of its own.

A registration is an intent (country, journey, email) that becomes an account only when the person proves they control the inbox and then
enrols a strong authenticator. Email ownership is exactly that: ownership of an inbox. It is not a legal name, an identity check, a
directorship or authority to act for a company, so nothing here ever produces a verified person, a verified organization or an Owner.
Every refusal is the safe one: an unknown country, a missing nonce, a stale or replayed token is a denial, never a success.
"""

from __future__ import annotations

import re
import secrets
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from hashlib import sha256


class RegistrationError(Exception):
    def __init__(self, code: str, detail: str = "", *, persist: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail
        self.persist = (
            persist  # something was written that must survive the refusal (an attempt count)
        )


class Journey(StrEnum):
    PERSONAL = "PERSONAL"  # "For myself"
    ORGANIZATION = "ORGANIZATION"  # "For an organization": selects the next journey only


class State(StrEnum):
    INITIATED = "INITIATED"
    VERIFICATION_SENT = "VERIFICATION_SENT"
    VERIFIED = "VERIFIED"  # inbox proved; the account exists, no authenticator yet
    COMPLETED = "COMPLETED"  # an authenticator is enrolled
    EXPIRED = "EXPIRED"
    RISK_HOLD = "RISK_HOLD"


FINAL = frozenset({State.COMPLETED, State.EXPIRED, State.RISK_HOLD})
TOKEN_TTL = timedelta(
    minutes=30
)  # "short-lived": long enough to open a mailbox, short enough to be worthless later
INTENT_TTL = timedelta(hours=24)  # an abandoned request is purged after this (S8, AC-11)
RESEND_MIN_GAP = timedelta(seconds=60)
MAX_RESENDS = 3
MAX_CONFIRM_ATTEMPTS = 5
MAX_INTENTS_PER_ADDRESS_HOUR = (
    5  # one address cannot be used to flood anyone, and a refresh never makes a second request
)
NONCE_TTL = timedelta(minutes=30)
GENERIC_MESSAGE = "If this address can be used, check your inbox for next steps."
EXPIRED_MESSAGE = "This link is no longer valid. Request a new link."
RISK_MESSAGE = "We couldn't complete registration securely. Contact support or try again later."

# ISO 3166-1 alpha-2 (the user-assigned codes XK and the exceptional reservations are not accepted: an unknown code is never guessed)
COUNTRIES = frozenset(
    [
        "AD",
        "AE",
        "AF",
        "AG",
        "AI",
        "AL",
        "AM",
        "AO",
        "AQ",
        "AR",
        "AS",
        "AT",
        "AU",
        "AW",
        "AX",
        "AZ",
        "BA",
        "BB",
        "BD",
        "BE",
        "BF",
        "BG",
        "BH",
        "BI",
        "BJ",
        "BL",
        "BM",
        "BN",
        "BO",
        "BQ",
        "BR",
        "BS",
        "BT",
        "BV",
        "BW",
        "BY",
        "BZ",
        "CA",
        "CC",
        "CD",
        "CF",
        "CG",
        "CH",
        "CI",
        "CK",
        "CL",
        "CM",
        "CN",
        "CO",
        "CR",
        "CU",
        "CV",
        "CW",
        "CX",
        "CY",
        "CZ",
        "DE",
        "DJ",
        "DK",
        "DM",
        "DO",
        "DZ",
        "EC",
        "EE",
        "EG",
        "EH",
        "ER",
        "ES",
        "ET",
        "FI",
        "FJ",
        "FK",
        "FM",
        "FO",
        "FR",
        "GA",
        "GB",
        "GD",
        "GE",
        "GF",
        "GG",
        "GH",
        "GI",
        "GL",
        "GM",
        "GN",
        "GP",
        "GQ",
        "GR",
        "GS",
        "GT",
        "GU",
        "GW",
        "GY",
        "HK",
        "HM",
        "HN",
        "HR",
        "HT",
        "HU",
        "ID",
        "IE",
        "IL",
        "IM",
        "IN",
        "IO",
        "IQ",
        "IR",
        "IS",
        "IT",
        "JE",
        "JM",
        "JO",
        "JP",
        "KE",
        "KG",
        "KH",
        "KI",
        "KM",
        "KN",
        "KP",
        "KR",
        "KW",
        "KY",
        "KZ",
        "LA",
        "LB",
        "LC",
        "LI",
        "LK",
        "LR",
        "LS",
        "LT",
        "LU",
        "LV",
        "LY",
        "MA",
        "MC",
        "MD",
        "ME",
        "MF",
        "MG",
        "MH",
        "MK",
        "ML",
        "MM",
        "MN",
        "MO",
        "MP",
        "MQ",
        "MR",
        "MS",
        "MT",
        "MU",
        "MV",
        "MW",
        "MX",
        "MY",
        "MZ",
        "NA",
        "NC",
        "NE",
        "NF",
        "NG",
        "NI",
        "NL",
        "NO",
        "NP",
        "NR",
        "NU",
        "NZ",
        "OM",
        "PA",
        "PE",
        "PF",
        "PG",
        "PH",
        "PK",
        "PL",
        "PM",
        "PN",
        "PR",
        "PS",
        "PT",
        "PW",
        "PY",
        "QA",
        "RE",
        "RO",
        "RS",
        "RU",
        "RW",
        "SA",
        "SB",
        "SC",
        "SD",
        "SE",
        "SG",
        "SH",
        "SI",
        "SJ",
        "SK",
        "SL",
        "SM",
        "SN",
        "SO",
        "SR",
        "SS",
        "ST",
        "SV",
        "SX",
        "SY",
        "SZ",
        "TC",
        "TD",
        "TF",
        "TG",
        "TH",
        "TJ",
        "TK",
        "TL",
        "TM",
        "TN",
        "TO",
        "TR",
        "TT",
        "TV",
        "TW",
        "TZ",
        "UA",
        "UG",
        "UM",
        "US",
        "UY",
        "UZ",
        "VA",
        "VC",
        "VE",
        "VG",
        "VI",
        "VN",
        "VU",
        "WF",
        "WS",
        "YE",
        "YT",
        "ZA",
        "ZM",
        "ZW",
    ]
)

_LOCALE = re.compile(r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8}){0,4}$")  # a BCP 47 tag, shape only
_TOKEN_SHAPE = re.compile(r"^[A-Za-z0-9_-]{32,128}$")
_REQUEST_SHAPE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")


def country_of(raw: str) -> str:
    """The residence country the person chose, as an ISO code. Never inferred from an address, a language or a phone prefix, and there is
    no default: an empty or unknown value is refused."""
    code = (raw or "").strip().upper()
    if code not in COUNTRIES:
        raise RegistrationError("COUNTRY_INVALID", "choose a country from the list")
    return code


def locale_of(raw: str | None) -> str:
    """A BCP 47 tag for the language of the notices. Anything that is not one falls back to `und` (undetermined), never to a country."""
    value = (raw or "").strip()
    return value if _LOCALE.match(value) else "und"


def journey_of(raw: str) -> Journey:
    try:
        return Journey(str(raw).strip().upper())
    except ValueError as e:
        raise RegistrationError("INTENT_INVALID", "choose For myself or For an organization") from e


def canonical_email(raw: str) -> str:
    """The approved canonicalization: Unicode normalization (NFC), the domain converted to its ASCII form and lower-cased, the local part
    kept exactly as typed apart from case, which mail systems do not distinguish. It does NOT strip +tags, remove dots or rewrite
    providers' aliases: two addresses that differ in any of those are two addresses. Anything that could smuggle a header or a second
    recipient is refused."""
    value = unicodedata.normalize("NFC", (raw or "").strip())
    if not value or len(value) > 254 or value.count("@") != 1:
        raise RegistrationError("EMAIL_INVALID", "enter a valid email address")
    local, domain = value.split("@")
    if not 1 <= len(local) <= 64 or any(
        ord(c) < 33 or ord(c) == 127 or c in '<>,;:"\\' for c in local
    ):
        raise RegistrationError("EMAIL_INVALID", "enter a valid email address")
    try:
        ascii_domain = domain.encode("idna").decode("ascii").lower()
    except UnicodeError as e:
        raise RegistrationError("EMAIL_INVALID", "enter a valid email address") from e
    if (
        not re.match(r"^[a-z0-9](?:[a-z0-9.\-]{0,251}[a-z0-9])?\.[a-z]{2,}$", ascii_domain)
        or ".." in ascii_domain
    ):
        raise RegistrationError("EMAIL_INVALID", "enter a valid email address")
    return f"{local.lower()}@{ascii_domain}"


def mask(address: str) -> str:
    """`j***@example.com`: enough for a person to recognise their own address, not enough to harvest it."""
    local, _, domain = address.partition("@")
    return f"{local[:1]}***@{domain}"


def new_token() -> tuple[str, bytes]:
    """(the secret to put in the email, what to store). Only the hash is kept, so a database read cannot activate an account."""
    token = secrets.token_urlsafe(32)
    return token, token_hash(token)


def token_hash(token: str) -> bytes:
    return sha256(token.encode("utf-8")).digest()


def token_shaped(token: str) -> bool:
    return bool(_TOKEN_SHAPE.match(token or ""))


def request_shaped(ref: str) -> bool:
    return bool(_REQUEST_SHAPE.match(ref or ""))


def new_request_ref() -> str:
    return secrets.token_urlsafe(18)


# ------------------------------------------------------------------------------------------------ decisions


@dataclass(frozen=True, slots=True)
class Intent:
    state: State
    token_hash: bytes | None
    token_expires_at: datetime | None
    resend_count: int
    last_sent_at: datetime | None
    confirm_attempts: int
    expires_at: datetime
    email_hash: str


def may_resend(i: Intent, now: datetime) -> bool:
    """A resend is allowed only while the request is open, not too soon after the last and a limited number of times."""
    if i.state not in (State.INITIATED, State.VERIFICATION_SENT) or now >= i.expires_at:
        return False
    if i.resend_count >= MAX_RESENDS:
        return False
    return i.last_sent_at is None or now - i.last_sent_at >= RESEND_MIN_GAP


def check_token(i: Intent, presented: str, now: datetime) -> str | None:
    """None if the token redeems this request now; otherwise the reason. The caller shows every reason as the same expired-or-invalid
    message (S8), so a stranger learns nothing about whose request it was or whether an account exists."""
    if i.state is not State.VERIFICATION_SENT:
        return "REQUEST_NOT_OPEN"  # replayed (already used), expired, held or never sent
    if now >= i.expires_at:
        return "REQUEST_EXPIRED"
    if i.confirm_attempts >= MAX_CONFIRM_ATTEMPTS:
        return "TOO_MANY_ATTEMPTS"
    if i.token_hash is None or i.token_expires_at is None or now >= i.token_expires_at:
        return "TOKEN_EXPIRED"
    if not token_shaped(presented) or not secrets.compare_digest(
        token_hash(presented), i.token_hash
    ):
        return "TOKEN_MISMATCH"
    return None


@dataclass(frozen=True, slots=True)
class Route:
    """What the person does next. `account` and `identity` are separate facts: an active account is not an identity check."""

    next_step: str
    account: str
    identity: str
    organization: str


def route_for(
    state: State, journey: Journey, *, authenticator_enrolled: bool, invitation: bool
) -> Route:
    if state is State.RISK_HOLD:
        return Route("CONTACT_SUPPORT", "NONE", "NOT_VERIFIED", "NONE")
    if state in (State.INITIATED, State.VERIFICATION_SENT):
        return Route("VERIFY_EMAIL", "NONE", "NOT_VERIFIED", "NONE")
    if state is State.EXPIRED:
        return Route("REQUEST_NEW_LINK", "NONE", "NOT_VERIFIED", "NONE")
    if invitation:
        step = "ACCEPT_INVITATION" if authenticator_enrolled else "ENROLL_AUTHENTICATOR"
        return Route(step, "ACTIVE", "NOT_VERIFIED", "NONE")
    if not authenticator_enrolled:
        return Route("ENROLL_AUTHENTICATOR", "ACTIVE", "NOT_VERIFIED", "NONE")
    if journey is Journey.ORGANIZATION:
        return Route(
            "ORGANIZATION_CLAIM", "ACTIVE", "NOT_VERIFIED", "NONE"
        )  # a provisional claim, then 13B: no badge, no Owner
    return Route("PERSONAL_WORKSPACE", "ACTIVE", "NOT_VERIFIED", "NONE")
