"""The partner Verify API: applications, credentials and access tokens (ZID-EXT-IDV-001 sections 05, WF-05, 10, 11 SEC-01 to SEC-04; CR-149). Pure rules, no I/O.

A partner is a company that uses Zoiko iD to verify ITS customers. It creates an APPLICATION for one environment (SANDBOX or PRODUCTION), and gets a CREDENTIAL for that application: a public
client id and a secret that is shown once and kept only as a hash. Its SERVER exchanges the pair for a short-lived ACCESS TOKEN carrying the scopes the application was granted, and uses that token
on every call. Nothing here touches a browser or an end user: the applicant gets only a session-bound launch link, never these credentials.

Rules that follow the document:
- a credential belongs to one application and one environment; a token can never be used across environments or applications (the token records both and the API checks them);
- the secret is never stored and never shown again; a lost secret means a new credential, not a recovery;
- rotation gives the new credential at once and lets the old one keep working for a bounded overlap, so a partner can deploy without downtime; revoking ends a credential and every token issued
  from it immediately;
- scopes only narrow: a token carries the scopes the application holds, or a subset the caller asks for, never more;
- PRODUCTION is not available from here until the production gate (KYB, contract, certification) has passed, so an application can be created for SANDBOX only.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from datetime import timedelta
from enum import StrEnum
from urllib.parse import urlsplit


class PartnerError(Exception):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail or code.replace("_", " ").capitalize() + "."


class Environment(StrEnum):
    SANDBOX = "SANDBOX"
    PRODUCTION = "PRODUCTION"


class AppStatus(StrEnum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    RETIRED = "RETIRED"


APP_TRANSITIONS: dict[AppStatus, frozenset[AppStatus]] = {
    AppStatus.ACTIVE: frozenset({AppStatus.SUSPENDED, AppStatus.RETIRED}),
    AppStatus.SUSPENDED: frozenset({AppStatus.ACTIVE, AppStatus.RETIRED}),
    AppStatus.RETIRED: frozenset(),
}


class CredentialStatus(StrEnum):
    ACTIVE = "ACTIVE"
    ROTATING = "ROTATING"  # still valid until `expires_at`, so a deployment can switch over; a newer credential exists
    REVOKED = "REVOKED"


# The scopes a partner application can hold (WF-05, section 10). Narrow by default: an application is given only what it asks for.
SCOPES = (
    "products:read",
    "workflows:read",
    "verifications:create",
    "verifications:read",
    "verifications:list",
    "verifications:resume",
    "verifications:recheck",
    "decisions:read",
    "webhooks:read",
    "webhooks:write",
    "usage:read",
    "applications:submit",
)
# What a new SANDBOX application gets when it does not ask for a narrower set: everything it needs to run the whole sandbox journey, but not the call that asks for production.
DEFAULT_SANDBOX_SCOPES = tuple(s for s in SCOPES if s != "applications:submit")

MAX_APPLICATIONS_PER_TENANT = 25
MAX_LIVE_CREDENTIALS_PER_APPLICATION = (
    4  # the current one, one rotating, and room for two more while a team moves over
)
MAX_REDIRECT_ORIGINS = 20
TOKEN_TTL = timedelta(minutes=15)
CREDENTIAL_TTL = timedelta(days=365)
DEFAULT_OVERLAP = timedelta(hours=24)
MAX_OVERLAP = timedelta(days=7)

CLIENT_PREFIX = "zpc_"
SECRET_PREFIX = "zps_"
TOKEN_PREFIX = "zpt_"
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._\-]{1,78}[A-Za-z0-9]$")


def check_name(name: str) -> str:
    clean = " ".join(name.split())
    if not _NAME.fullmatch(clean):
        raise PartnerError(
            "VALIDATION_FAILED",
            "An application name is 3 to 80 letters, numbers, spaces, dots, dashes or underscores, starting and ending with a letter or number.",
        )
    return clean


def check_environment(env: str, *, production_open: bool = False) -> Environment:
    try:
        value = Environment(env.upper())
    except ValueError as e:
        raise PartnerError("VALIDATION_FAILED", "The environment is SANDBOX or PRODUCTION.") from e
    if value is Environment.PRODUCTION and not production_open:
        raise PartnerError(
            "PRODUCTION_NOT_APPROVED",
            "Production is opened after your company is verified, your terms are signed and your sandbox integration is certified. Build and test in the sandbox first.",
        )
    return value


def check_scopes(
    requested: list[str] | tuple[str, ...] | None, held: tuple[str, ...] | list[str] | None = None
) -> tuple[str, ...]:
    """The scopes to grant: the ones asked for, each known and (when `held` is given) already held. Never more than the application holds."""
    if requested is None:
        return tuple(held) if held is not None else DEFAULT_SANDBOX_SCOPES
    wanted = tuple(dict.fromkeys(s.strip() for s in requested if s.strip()))
    if not wanted:
        raise PartnerError("VALIDATION_FAILED", "Choose at least one scope.")
    unknown = [s for s in wanted if s not in SCOPES]
    if unknown:
        raise PartnerError("VALIDATION_FAILED", f"Unknown scope: {unknown[0]}.")
    if held is not None:
        beyond = [s for s in wanted if s not in held]
        if beyond:
            raise PartnerError(
                "INVALID_SCOPE", f"This application does not hold the scope {beyond[0]}."
            )
    return wanted


def check_origins(origins: list[str] | None) -> tuple[str, ...]:
    """Exact allowlisted origins for hosted-flow return addresses: https with a host, no path, query, fragment or wildcard. A local development origin may use http."""
    out: list[str] = []
    for raw in origins or []:
        url = raw.strip()
        parts = urlsplit(url)
        loopback = parts.hostname in ("localhost", "127.0.0.1", "::1")
        if (
            not parts.hostname
            or "*" in url
            or parts.path not in ("", "/")
            or parts.query
            or parts.fragment
            or parts.username
            or (parts.scheme != "https" and not (parts.scheme == "http" and loopback))
        ):
            raise PartnerError(
                "VALIDATION_FAILED",
                f"{url!r} is not an exact https origin such as https://app.example.com.",
            )
        origin = f"{parts.scheme}://{parts.netloc}".lower()
        if origin not in out:
            out.append(origin)
    if len(out) > MAX_REDIRECT_ORIGINS:
        raise PartnerError("VALIDATION_FAILED", f"At most {MAX_REDIRECT_ORIGINS} origins.")
    return tuple(out)


def check_app_transition(current: AppStatus, target: AppStatus) -> AppStatus:
    if target not in APP_TRANSITIONS.get(current, frozenset()):
        raise PartnerError(
            "APPLICATION_STATE_INVALID",
            f"An application that is {current.value.lower()} cannot become {target.value.lower()}.",
        )
    return target


# ------------------------------------------------------------------------------- secrets


def new_client_id() -> str:
    return CLIENT_PREFIX + secrets.token_hex(10)


def new_secret() -> str:
    """256 bits of randomness: strong enough that a fast hash is the right way to keep it (there is nothing to guess), and it is shown once."""
    return SECRET_PREFIX + secrets.token_urlsafe(32)


def new_token() -> str:
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def hash_secret(value: str) -> bytes:
    return hashlib.sha256(value.encode()).digest()


def secret_matches(presented: str, stored_hash: bytes) -> bool:
    """Constant-time comparison of the hash of what was presented against the stored hash."""
    return hmac.compare_digest(hash_secret(presented), bytes(stored_hash))


def secret_hint(value: str) -> str:
    """The last four characters, so a person can tell two credentials apart without the secret being recoverable."""
    return value[-4:]


def mask(value: str) -> str:
    return value[:4] + "…" + value[-4:] if len(value) > 10 else "…"
