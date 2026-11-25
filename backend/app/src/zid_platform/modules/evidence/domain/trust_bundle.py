"""The issuer's signed trust bundle, and when a verifier must refuse it (ZID-CP-WF08 v2.0 .06; CR-170). Pure domain, no I/O.

A verifier needs to know which keys to trust, which are retired and which were compromised and from when. The issuer publishes that as a signed, versioned, expiring bundle. A verifier that cached an old bundle, was handed one for a
different issuer or environment, or was handed an older version than it already saw, must refuse it: otherwise a stale bundle could keep a stolen key trusted, or a substituted one could swap in someone else's.

The signature on the bundle is checked by the caller against the keys the verifier already pinned. This module checks the rest.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

BUNDLE_TYP = "zid-trust-bundle+jwt"
SCHEMA = "zoiko.trust-bundle.v1"
TTL = timedelta(hours=24)  # a verifier that cannot refresh within a day is told its trust is stale
MAX_TTL = timedelta(days=7)
RECEIPT_SCHEMAS = ("zoiko.receipt.v1",)
LIMITS = (
    "This bundle lists signing keys and what is known about them. It says nothing about whether any decision was correct, and it is not a qualified or notarized statement. Post-quantum or hybrid suites are not offered."
)


def key_entry(jwk: Mapping[str, Any], *, active_kid: str, compromised: Mapping[str, datetime]) -> dict[str, Any]:
    """One key as a verifier needs it. A compromised key stays listed, with the moment from which its signatures prove nothing, so history before that moment still verifies."""
    kid = str(jwk["kid"])
    since = compromised.get(kid)
    state = "COMPROMISED" if since else "ACTIVE" if kid == active_kid else "RETIRED"
    out: dict[str, Any] = {"kid": kid, "state": state, "use": "evidence-signing", "alg": jwk.get("alg"), "jwk": dict(jwk)}
    if since:
        out["compromised_from"] = since.astimezone(UTC).isoformat()
    return out


def bundle_claims(*, issuer: str, environment: str, keys: Sequence[Mapping[str, Any]], active_kid: str, compromised: Mapping[str, datetime], version: int, now: datetime, ttl: timedelta = TTL) -> dict[str, Any]:
    ttl = min(ttl, MAX_TTL)
    entries = [key_entry(k, active_kid=active_kid, compromised=compromised) for k in keys]
    return {
        "schema": SCHEMA, "issuer": issuer, "environment": environment, "version": version, "issued_at": now.astimezone(UTC).isoformat(), "expires_at": (now + ttl).astimezone(UTC).isoformat(),
        "receipt_schemas": list(RECEIPT_SCHEMAS), "keys": entries, "active_kid": active_kid, "pq_hybrid": "NOT_OFFERED", "limits": LIMITS,
    }  # fmt: skip


def version_from(keys: Sequence[Mapping[str, Any]], compromised: Mapping[str, datetime]) -> int:
    """A number that only goes up when the trusted set or a key's standing changes: the count of keys ever published plus the count compromised, so rotating or declaring a compromise always raises it."""
    return len(keys) * 10 + len(compromised)


def bundle_problems(
    claims: Mapping[str, Any], *, expected_issuer: str, expected_environment: str, now: datetime, last_seen_version: int | None = None, required_kid: str | None = None
) -> list[str]:  # fmt: skip
    """Why a verifier must not use this bundle. Every reason is named."""
    out: list[str] = []
    if claims.get("schema") != SCHEMA:
        out.append("TRUST_BUNDLE_SCHEMA_UNSUPPORTED")  # an old verifier meeting a mandatory change refuses rather than guesses
        return out
    if claims.get("issuer") != expected_issuer:
        out.append("TRUST_BUNDLE_WRONG_ISSUER")  # substituted
    if claims.get("environment") != expected_environment:
        out.append("TRUST_BUNDLE_WRONG_ENVIRONMENT")  # a sandbox bundle never trusts production
    try:
        expires = datetime.fromisoformat(str(claims["expires_at"]))
        issued = datetime.fromisoformat(str(claims["issued_at"]))
        if expires <= now:
            out.append("TRUST_BUNDLE_EXPIRED")
        if issued > now + timedelta(minutes=5):
            out.append("TRUST_BUNDLE_FROM_THE_FUTURE")
        if expires - issued > MAX_TTL:
            out.append("TRUST_BUNDLE_LIFETIME_TOO_LONG")
    except (KeyError, ValueError):
        out.append("TRUST_BUNDLE_TIMES_INVALID")
    version = claims.get("version")
    if not isinstance(version, int) or isinstance(version, bool):
        out.append("TRUST_BUNDLE_VERSION_INVALID")
    elif last_seen_version is not None and version < last_seen_version:
        out.append("TRUST_BUNDLE_ROLLED_BACK")  # an older bundle than one already seen could resurrect a compromised key
    if required_kid is not None and required_kid not in {k.get("kid") for k in claims.get("keys", [])}:
        out.append("TRUST_BUNDLE_LACKS_REQUIRED_KEY")
    return sorted(set(out))


def key_standing(claims: Mapping[str, Any], kid: str, signed_at: datetime) -> str:
    """Whether a signature made with `kid` at `signed_at` can be trusted according to this bundle: TRUSTED, TRUSTED_RETIRED (rotation retires gracefully, history stays valid), COMPROMISED_AT_SIGNING (it was signed after the compromise
    began, so it proves nothing) or UNKNOWN_KEY."""
    for k in claims.get("keys", []):
        if k.get("kid") != kid:
            continue
        if k.get("state") == "COMPROMISED":
            since = datetime.fromisoformat(str(k["compromised_from"]))
            return "COMPROMISED_AT_SIGNING" if signed_at >= since else "TRUSTED_RETIRED"
        return "TRUSTED" if k.get("state") == "ACTIVE" else "TRUSTED_RETIRED"
    return "UNKNOWN_KEY"
