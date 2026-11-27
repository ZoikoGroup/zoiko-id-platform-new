"""Binding an agent to an outside identity provider, as EVIDENCE (ZID-CP-WF07 v2.0 .03; A07-11, A07-12; CR-169). Pure domain, no I/O.

An identity provider can say "this token is for this agent object". That is a claim from another system. Here it is checked and kept as evidence, and it changes nothing about what the agent may do:

- the provider (its issuer, audience and signing keys) is allowlisted per company, and the keys are the ones the company pinned, never fetched from an address in a token;
- one binding ties one issuer and subject to one agent, in one environment; the same issuer and subject cannot be bound to a second agent, and an agent's display name or e-mail never joins anything;
- a token proves the binding only when its signature, issuer, audience, subject, expiry and (where the profile asks for it) a one-time id all check out, and when it is short-lived;
- what the token says (a blueprint, a sponsor, a lifecycle) is stored as an outside claim with where it came from. It never asserts a corporate authority and never activates an agent.

Okta and Entra are PROFILES: a map from their claim names to ours, tested here against a local issuer. They are not certified against the real vendors.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from typing import Any

from zid_platform.modules.machine.domain.agents import MachineError

MAX_TOKEN_LIFETIME = 600  # seconds: an agent identity token is short-lived
LEEWAY = 60
MAX_KEYS = 4
VALID_ALGS = ("ES256", "ES384", "EdDSA", "RS256")
PRIVATE_PARTS = ("d", "p", "q", "dp", "dq", "qi", "k")

PROFILES: dict[str, dict[str, Any]] = {
    "generic_oidc": {"label": "Generic OpenID Connect", "claims": {"blueprint": "blueprint", "sponsor": "sponsor", "lifecycle": "lifecycle"}, "needs_jti": False, "certified": False},
    "okta": {
        "label": "Okta (Agent SSO profile)", "claims": {"blueprint": "agent_type", "sponsor": "owner", "lifecycle": "status"}, "needs_jti": True, "certified": False,
        "note": "Claim names follow Okta's published agent descriptions as understood here. Not certified against an Okta tenant.",
    },
    "entra": {
        "label": "Microsoft Entra (Agent ID profile)", "claims": {"blueprint": "xms_blueprint_id", "sponsor": "xms_sponsor", "lifecycle": "xms_agent_state"}, "needs_jti": True, "certified": False,
        "note": "Claim names follow Microsoft's published agent identity descriptions as understood here. Not certified against an Entra tenant.",
    },
}  # fmt: skip
EXTERNAL_CLAIMS = ("blueprint", "sponsor", "lifecycle")


def check_profile(name: str | None) -> str:
    if name not in PROFILES:
        raise MachineError("IDP_BINDING_INVALID", "a profile is " + ", ".join(PROFILES) + ".")
    return str(name)


def check_issuer(issuer: str | None) -> str:
    """An exact https address, no query, fragment or credentials: it is compared as text, never fetched."""
    s = (issuer or "").strip()
    if not s.startswith("https://") or len(s) > 300 or any(ch in s for ch in ("?", "#", "@", " ")):
        raise MachineError(
            "IDP_BINDING_INVALID",
            "an issuer is an exact https address with no query, fragment or credentials",
        )
    return s


def check_keys(keys: Any, algs: Sequence[str]) -> tuple[list[dict[str, Any]], list[str]]:
    """The issuer's PUBLIC keys, pinned by the company. A private key, a key with no id, a duplicate id or a remote reference is refused."""
    if not isinstance(keys, list) or not 1 <= len(keys) <= MAX_KEYS:
        raise MachineError("IDP_BINDING_INVALID", f"pin 1 to {MAX_KEYS} public keys")
    clean: list[dict[str, Any]] = []
    seen: set[str] = set()
    for k in keys:
        if (
            not isinstance(k, dict)
            or not isinstance(k.get("kid"), str)
            or not k["kid"]
            or len(k["kid"]) > 100
        ):
            raise MachineError("IDP_BINDING_INVALID", "each key is an object with a key id")
        if any(p in k for p in PRIVATE_PARTS):
            raise MachineError(
                "IDP_BINDING_INVALID", "pin public keys only: this one holds private material"
            )
        if any(p in k for p in ("jku", "x5u")):
            raise MachineError(
                "IDP_BINDING_INVALID", "a key is pinned, not fetched from an address"
            )
        if k["kid"] in seen:
            raise MachineError("IDP_BINDING_INVALID", "two keys have the same id")
        seen.add(k["kid"])
        clean.append({kk: vv for kk, vv in k.items() if isinstance(kk, str) and len(kk) <= 20})
    chosen = list(algs) or ["ES256"]
    if not set(chosen) <= set(VALID_ALGS):
        raise MachineError("IDP_BINDING_INVALID", "allowed algorithms are " + ", ".join(VALID_ALGS))
    return clean, chosen


def check_audience(audience: str | None) -> str:
    s = (audience or "").strip()
    if not 3 <= len(s) <= 300:
        raise MachineError(
            "IDP_BINDING_INVALID",
            "name the audience the provider puts in its tokens for this agent",
        )
    return s


def check_subject(subject: str | None) -> str:
    s = (subject or "").strip()
    if not 1 <= len(s) <= 200:
        raise MachineError(
            "IDP_BINDING_INVALID",
            "name the provider's own identifier for the agent (the token's subject)",
        )
    return s


def _num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def claim_problems(
    claims: Mapping[str, Any],
    *,
    issuer: str,
    audience: str,
    subject: str,
    now: datetime,
    needs_jti: bool,
    max_lifetime: int = MAX_TOKEN_LIFETIME,
) -> list[str]:
    """Why a token whose signature already verified still does not prove this binding. Every reason is named."""
    out: list[str] = []
    if claims.get("iss") != issuer:
        out.append("IDP_ISSUER_MISMATCH")
    aud = claims.get("aud")
    if audience not in (aud if isinstance(aud, list) else [aud]):
        out.append("IDP_AUDIENCE_MISMATCH")  # a token made for another service is not for this one
    if claims.get("sub") != subject:
        out.append("IDP_SUBJECT_MISMATCH")
    ts = now.timestamp()
    exp, iat, nbf = claims.get("exp"), claims.get("iat"), claims.get("nbf")
    if not _num(exp):
        out.append("IDP_TOKEN_NO_EXPIRY")
    elif exp + LEEWAY <= ts:  # type: ignore[operator]
        out.append("IDP_TOKEN_EXPIRED")
    if not _num(iat):
        out.append("IDP_TOKEN_NO_ISSUE_TIME")
    elif _num(exp) and exp - iat > max_lifetime:  # type: ignore[operator]
        out.append("IDP_TOKEN_TOO_LONG_LIVED")
    if _num(iat) and iat - LEEWAY > ts:  # type: ignore[operator]
        out.append("IDP_TOKEN_FROM_THE_FUTURE")
    if _num(nbf) and nbf - LEEWAY > ts:  # type: ignore[operator]
        out.append("IDP_TOKEN_NOT_YET_VALID")
    if needs_jti and not (isinstance(claims.get("jti"), str) and claims["jti"]):
        out.append("IDP_TOKEN_NO_ID")
    return out


def external_claims(profile: str, claims: Mapping[str, Any]) -> dict[str, str]:
    """What the provider said about the agent, under OUR names, as short plain text. Nothing else from the token is kept, and nothing here ever becomes authority."""
    mapping = PROFILES[profile]["claims"]
    out: dict[str, str] = {}
    for ours in EXTERNAL_CLAIMS:
        raw = claims.get(mapping[ours])
        if isinstance(raw, (str, int)) and not isinstance(raw, bool):
            out[ours] = str(raw)[:200]
    return out


def token_digest(token: str) -> str:
    """Remembered instead of the token: it cannot be turned back into one."""
    return "sha256:" + hashlib.sha256(token.encode()).hexdigest()


def keys_fingerprint(keys: Iterable[Mapping[str, Any]]) -> str:
    return (
        "sha256:"
        + hashlib.sha256(
            json.dumps(
                sorted((dict(k) for k in keys), key=lambda k: str(k.get("kid"))), sort_keys=True
            ).encode()
        ).hexdigest()
    )


def binding_state(
    *,
    status: str,
    expires_at: datetime | None,
    now: datetime,
    last_ok_at: datetime | None,
    stale_after_s: int,
) -> str:
    """ACTIVE only while the provider has been heard from lately. A connector that has gone quiet is STALE and proves nothing: there is no implicit fallback."""
    if status != "ACTIVE":
        return status
    if expires_at is not None and expires_at <= now:
        return "EXPIRED"
    if last_ok_at is None or (now - last_ok_at).total_seconds() > stale_after_s:
        return "STALE"
    return "ACTIVE"
