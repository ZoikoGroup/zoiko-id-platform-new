"""API-based agents (CR-093): an agent whose software proof is a registered API client instead of a container image.

The container path proves WHAT is running (an attestor compares the running image to the approved digest). An API client cannot prove that: it
can only prove it holds a private key. So the proof here is weaker, and the platform says so in code: an API-bound agent may hold only tools that
read or can be undone, never a tool that moves money or cannot be reversed, and never one at a high risk class. Everything else is the same
registry: the same two-person approval of the build, the same blueprint release, the same activation gates, the same kill paths.

Pure domain, standard library only. Only PUBLIC keys are ever accepted or stored.
"""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

from zid_platform.modules.machine.domain.governance import digest, has_wildcard

MAX_KEYS = 5
MAX_AUDIENCES = 5
MIN_RSA_BITS = 2048
API_MAX_RISK_CLASS = 1  # an API-bound agent may not hold a tool above this risk class
API_ALLOWED_SIDE_EFFECTS = frozenset({"NONE", "REVERSIBLE"})
API_ATTESTATION_CAP = (
    1  # the highest runtime attestation an API client can show: it proves a key, never the code
)

# any of these in a JWK means it carries a secret or a remote reference, and is refused
PRIVATE_MEMBERS = frozenset({"d", "p", "q", "dp", "dq", "qi", "oth", "k"})
REMOTE_MEMBERS = frozenset({"x5u", "jku", "x5c"})


def _b64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def jwk_problems(jwk: Mapping[str, Any]) -> list[str]:
    """Why a key may not be registered. Public keys only; named curves and sizes only; a `kid` so a rotation can be traced."""
    out: list[str] = []
    kty = jwk.get("kty")
    if set(jwk) & PRIVATE_MEMBERS:
        out.append("JWK_PRIVATE_MATERIAL")  # never accept, and never echo, a secret
    if set(jwk) & REMOTE_MEMBERS:
        out.append("JWK_REMOTE_REFERENCE")  # a key fetched from a URL could change after approval
    kid = jwk.get("kid")
    if not isinstance(kid, str) or not 1 <= len(kid) <= 64:
        out.append("JWK_KID_REQUIRED")
    if jwk.get("use") == "enc" or jwk.get("alg", "") in ("none", "HS256", "HS384", "HS512"):
        out.append("JWK_NOT_A_SIGNING_KEY")
    if kty == "EC":
        if (
            jwk.get("crv") not in ("P-256", "P-384")
            or not isinstance(jwk.get("x"), str)
            or not isinstance(jwk.get("y"), str)
        ):
            out.append("JWK_CURVE_NOT_ALLOWED")
    elif kty == "OKP":
        if jwk.get("crv") != "Ed25519" or not isinstance(jwk.get("x"), str):
            out.append("JWK_CURVE_NOT_ALLOWED")
    elif kty == "RSA":
        n, e = jwk.get("n"), jwk.get("e")
        try:
            bits = len(_b64(n).lstrip(b"\x00")) * 8 if isinstance(n, str) else 0
        except Exception:  # noqa: BLE001 - not base64url at all
            bits = 0
        if bits < MIN_RSA_BITS or not isinstance(e, str):
            out.append("JWK_RSA_TOO_SMALL")
    else:
        out.append("JWK_TYPE_NOT_ALLOWED")
    return out


def jwks_problems(jwks: Mapping[str, Any]) -> list[str]:
    keys = jwks.get("keys")
    if not isinstance(keys, list) or not 1 <= len(keys) <= MAX_KEYS:
        return [f"JWKS_NEEDS_1_TO_{MAX_KEYS}_KEYS"]
    if set(jwks) - {"keys"}:
        return ["JWKS_UNKNOWN_MEMBER"]
    out: list[str] = []
    seen: set[str] = set()
    for k in keys:
        if not isinstance(k, dict):
            out.append("JWK_INVALID")
            continue
        out.extend(jwk_problems(k))
        kid = k.get("kid")
        if isinstance(kid, str):
            if kid in seen:
                out.append("JWK_KID_DUPLICATE")
            seen.add(kid)
    return list(dict.fromkeys(out))


def thumbprint(jwk: Mapping[str, Any]) -> str:
    """RFC 7638: SHA-256 over the required members in lexicographic order, no whitespace, base64url without padding."""
    members = {
        "EC": ("crv", "kty", "x", "y"),
        "OKP": ("crv", "kty", "x"),
        "RSA": ("e", "kty", "n"),
    }[str(jwk["kty"])]
    canonical = json.dumps({m: jwk[m] for m in members}, separators=(",", ":"), sort_keys=True)
    return (
        base64.urlsafe_b64encode(hashlib.sha256(canonical.encode("ascii")).digest())
        .rstrip(b"=")
        .decode("ascii")
    )


def audience_problems(audiences: Sequence[str]) -> list[str]:
    if not 1 <= len(audiences) <= MAX_AUDIENCES:
        return [f"AUDIENCES_NEED_1_TO_{MAX_AUDIENCES}"]
    out = []
    for a in audiences:
        if not isinstance(a, str) or has_wildcard(a) or len(a) > 200 or any(ord(c) < 33 for c in a):
            out.append("AUDIENCE_NOT_EXACT")
    return list(dict.fromkeys(out))


def descriptor(client_id: str, jwks: Mapping[str, Any], audiences: Sequence[str]) -> dict[str, Any]:
    """The public facts of one API client. Its digest is what a build, a blueprint release and a binding all name."""
    return {
        "kind": "API_CLIENT",
        "client_id": client_id,
        "thumbprints": sorted(thumbprint(k) for k in jwks["keys"]),
        "audiences": sorted(audiences),
    }


def descriptor_digest(client_id: str, jwks: Mapping[str, Any], audiences: Sequence[str]) -> str:
    return digest(descriptor(client_id, jwks, audiences))


def api_tool_blockers(side_effect: str, risk_class: int) -> list[str]:
    """What an API-bound agent may never hold: a tool that cannot be undone, one that moves money, or one above the API risk ceiling."""
    out = []
    if side_effect not in API_ALLOWED_SIDE_EFFECTS:
        out.append("API_AGENT_TOOL_NOT_ALLOWED")
    if risk_class > API_MAX_RISK_CLASS and "API_AGENT_TOOL_NOT_ALLOWED" not in out:
        out.append("API_AGENT_TOOL_NOT_ALLOWED")
    return out


# ---------------------------------------------------------------- the credential path (CR-094)

ASSERTION_TYP = "api-client-assertion+jwt"
ASSERTION_ALGORITHMS = ("ES256", "Ed25519")
ASSERTION_MAX_LIFETIME_SECONDS = 60  # a one-time statement, not a session
ASSERTION_SKEW_SECONDS = 30
API_TOKEN_LIFETIME_SECONDS = 300
API_TOKEN_RISK_CLASS = 1  # what the token asks for: the attestation floor of this class is what API_ATTESTATION_CAP is held against
MAX_JTI = 128
_FLOOR_BY_RISK = {
    3: 3,
    2: 2,
}  # the same floors the image path uses (MachineState.attestation_floor_by_risk)


def assertion_problems(
    claims: Mapping[str, Any],
    *,
    client_id: str,
    token_audience: str,
    resources: Sequence[str],
    now_ts: int,
) -> list[str]:
    """Why a signed assertion (whose signature has ALREADY verified) cannot be accepted. The audience is exact (the platform's own token
    address); `resource` names one audience the client was registered with, exactly; the lifetime is short; there is a single-use id."""
    out: list[str] = []
    if claims.get("iss") != client_id or claims.get("sub") != client_id:
        out.append("ASSERTION_ISSUER_MISMATCH")
    if claims.get("aud") != token_audience:
        out.append("ASSERTION_AUDIENCE_MISMATCH")
    if claims.get("resource") not in set(resources):
        out.append("ASSERTION_RESOURCE_NOT_REGISTERED")
    jti = claims.get("jti")
    if not isinstance(jti, str) or not 8 <= len(jti) <= MAX_JTI or any(ord(c) < 33 for c in jti):
        out.append("ASSERTION_ID_REQUIRED")
    try:
        iat, exp = int(claims["iat"]), int(claims["exp"])
    except (KeyError, TypeError, ValueError):
        return [*out, "ASSERTION_TIMES_REQUIRED"]
    if exp - iat > ASSERTION_MAX_LIFETIME_SECONDS or exp <= iat:
        out.append("ASSERTION_TOO_LONG_LIVED")
    if exp + ASSERTION_SKEW_SECONDS < now_ts:
        out.append("ASSERTION_EXPIRED")
    if iat - ASSERTION_SKEW_SECONDS > now_ts:
        out.append("ASSERTION_FROM_THE_FUTURE")
    return list(dict.fromkeys(out))


def api_credential_blockers(
    *,
    agent_active: bool,
    binding_status: str,
    binding_valid: bool,
    build_revoked: bool,
    digest_matches_release: bool,
    frozen: bool,
    risk_level: int = API_TOKEN_RISK_CLASS,
) -> list[str]:
    """Whether the agent may receive a short-lived credential now. The attestation an API client can show is capped at `API_ATTESTATION_CAP`;
    the floor for the requested risk class is the one the image path uses, so a class that needs more than the cap is refused here."""
    out: list[str] = []
    if frozen:
        out.append("EMERGENCY_FROZEN")
    if not agent_active:
        out.append("AGENT_NOT_ACTIVE")
    if binding_status != "ACTIVE" or not binding_valid:
        out.append("API_BINDING_INACTIVE")
    if build_revoked or not digest_matches_release:
        out.append("BLUEPRINT_REVOKED" if build_revoked else "API_CLIENT_NOT_APPROVED")
    if API_ATTESTATION_CAP < _FLOOR_BY_RISK.get(risk_level, 0):
        out.append("ATTESTATION_INSUFFICIENT")
    return list(dict.fromkeys(out))
