"""Access-token and DPoP-proof claim rules (ZID-AUTH-001 §6.3-6.4, §7.1; RFC 9068, RFC 9449).

Pure domain: signatures are verified before these run (zid_crypto); here we decide whether the
verified claims are acceptable. Key rules:
- exact issuer and audience; bounded lifetime by risk class; clock skew bounded both ways;
- tenant, subject and token id are mandatory; authority and balances never travel in a token;
- a token bound to a key (`cnf.jkt`) is accepted only with a valid DPoP proof from that key; a
  bound token presented as a plain bearer token is rejected, never downgraded (AUTH §7.1, §28).
"""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

ACCESS_TOKEN_TYP = "at+jwt"
DPOP_TYP = "dpop+jwt"
LEEWAY = timedelta(seconds=30)
MAX_ACCESS_LIFETIME = {
    0: timedelta(minutes=15),
    1: timedelta(minutes=15),
    2: timedelta(minutes=5),
    3: timedelta(minutes=2),
}
DPOP_MAX_AGE = timedelta(seconds=60)
FORBIDDEN_CLAIMS = frozenset(
    {
        "authority",
        "grants",
        "mandate_balance",
        "remaining",
        "permissions",
        "credentials",
        "verification_evidence",
    }
)


class TokenError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


@dataclass(frozen=True, slots=True)
class AccessClaims:
    subject: str
    tenant_id: str
    client_id: str
    scope: frozenset[str]
    session_id: str | None
    jti: str
    issued_at: datetime
    expires_at: datetime
    risk_class: int
    acr: str | None = None
    amr: tuple[str, ...] = ()
    auth_time: datetime | None = None
    cnf_jkt: str | None = None  # DPoP key binding
    cnf_x5t: str | None = None  # mTLS certificate binding (RFC 8705 x5t#S256)
    actor: str | None = None  # RFC 8693 `act.sub`: who acts for the subject (token exchange)


def _time(claims: dict[str, Any], name: str, *, required: bool = True) -> datetime | None:
    v = claims.get(name)
    if v is None and not required:
        return None
    if not isinstance(v, int | float) or isinstance(v, bool):
        raise TokenError("TOKEN_CLAIM_INVALID", name)
    return datetime.fromtimestamp(v, UTC)


def _str(claims: dict[str, Any], name: str, *, required: bool = True) -> str | None:
    v = claims.get(name)
    if v is None and not required:
        return None
    if not isinstance(v, str) or not v or len(v) > 256:
        raise TokenError("TOKEN_CLAIM_INVALID", name)
    return v


def validate_access_claims(
    claims: dict[str, Any], *, issuer: str, audience: str, now: datetime
) -> AccessClaims:
    if claims.get("iss") != issuer:
        raise TokenError("TOKEN_ISSUER_INVALID")
    aud = claims.get("aud")
    if aud != audience and not (isinstance(aud, list) and aud == [audience]):
        raise TokenError("TOKEN_AUDIENCE_INVALID")  # exactly this resource, never a list of many
    if FORBIDDEN_CLAIMS & claims.keys():
        raise TokenError(
            "TOKEN_CLAIM_FORBIDDEN", ", ".join(sorted(FORBIDDEN_CLAIMS & claims.keys()))
        )
    iat, exp = _time(claims, "iat"), _time(claims, "exp")
    nbf = _time(claims, "nbf", required=False)
    if iat is None or exp is None:
        raise TokenError("TOKEN_CLAIM_INVALID", "iat/exp")
    risk = claims.get("risk_class", 1)
    if risk not in MAX_ACCESS_LIFETIME:
        raise TokenError("TOKEN_CLAIM_INVALID", "risk_class")
    if exp - iat > MAX_ACCESS_LIFETIME[risk] or exp <= iat:
        raise TokenError("TOKEN_LIFETIME_INVALID")
    if iat > now + LEEWAY or (nbf is not None and nbf > now + LEEWAY):
        raise TokenError("TOKEN_NOT_YET_VALID")
    if now >= exp + LEEWAY:
        raise TokenError("TOKEN_EXPIRED")
    cnf = claims.get("cnf")
    jkt = x5t = None
    if cnf is not None:  # exactly one binding: a DPoP key or an mTLS certificate
        if not isinstance(cnf, dict) or len(cnf) != 1:
            raise TokenError("TOKEN_CLAIM_INVALID", "cnf")
        jkt, x5t = cnf.get("jkt"), cnf.get("x5t#S256")
        if not isinstance(jkt or x5t, str):
            raise TokenError("TOKEN_CLAIM_INVALID", "cnf")
    act = claims.get("act")
    if act is not None and (not isinstance(act, dict) or not isinstance(act.get("sub"), str)):
        raise TokenError("TOKEN_CLAIM_INVALID", "act")
    scope = claims.get("scope", "")
    amr = claims.get("amr", [])
    if (
        not isinstance(scope, str)
        or not isinstance(amr, list)
        or not all(isinstance(a, str) for a in amr)
    ):
        raise TokenError("TOKEN_CLAIM_INVALID", "scope/amr")
    return AccessClaims(
        subject=_str(claims, "sub") or "",
        tenant_id=_str(claims, "tenant_id") or "",
        client_id=_str(claims, "client_id") or "",
        scope=frozenset(scope.split()),
        session_id=_str(claims, "sid", required=False),
        jti=_str(claims, "jti") or "",
        issued_at=iat,
        expires_at=exp,
        risk_class=risk,
        acr=_str(claims, "acr", required=False),
        amr=tuple(amr),
        auth_time=_time(claims, "auth_time", required=False),
        cnf_jkt=jkt,
        cnf_x5t=x5t,
        actor=act["sub"] if act else None,
    )


def normalize_htu(url: str) -> str:
    """RFC 9449 §4.3: compare scheme, host, port and path; never query or fragment."""
    p = urlsplit(url)
    if p.scheme not in ("https", "http") or not p.hostname:
        raise TokenError("DPOP_PROOF_INVALID", "htu")
    port = p.port
    default = {"https": 443, "http": 80}[p.scheme]
    host = p.hostname.lower() + (f":{port}" if port and port != default else "")
    return f"{p.scheme}://{host}{p.path or '/'}"


def access_token_hash(token: str) -> str:
    return (
        base64.urlsafe_b64encode(hashlib.sha256(token.encode("ascii")).digest())
        .rstrip(b"=")
        .decode()
    )


@dataclass(frozen=True, slots=True)
class DPoPProof:
    jti: str
    issued_at: datetime
    nonce: str | None = None  # server-issued nonce echoed by the client (RFC 9449 §8)


def validate_dpop_claims(
    header: dict[str, Any],
    payload: dict[str, Any],
    *,
    method: str,
    url: str,
    access_token: str | None,
    now: datetime,
    nonce: str | None = None,
) -> DPoPProof:
    """Checks on a DPoP proof whose signature already verified against its own embedded `jwk`.

    `access_token` is None at the token endpoint, where no token is presented and so no `ath`."""
    if header.get("typ") != DPOP_TYP or not isinstance(header.get("jwk"), dict):
        raise TokenError("DPOP_PROOF_INVALID", "typ/jwk")
    if payload.get("htm") != method.upper():
        raise TokenError("DPOP_PROOF_INVALID", "htm")
    htu = payload.get("htu")
    if not isinstance(htu, str) or normalize_htu(htu) != normalize_htu(url):
        raise TokenError("DPOP_PROOF_INVALID", "htu")
    jti = payload.get("jti")
    if not isinstance(jti, str) or not 8 <= len(jti) <= 128:
        raise TokenError("DPOP_PROOF_INVALID", "jti")
    iat = payload.get("iat")
    if not isinstance(iat, int | float) or isinstance(iat, bool):
        raise TokenError("DPOP_PROOF_INVALID", "iat")
    issued = datetime.fromtimestamp(iat, UTC)
    if issued > now + LEEWAY or now - issued > DPOP_MAX_AGE:
        raise TokenError("DPOP_PROOF_STALE")
    if access_token is not None and payload.get("ath") != access_token_hash(access_token):
        raise TokenError("DPOP_PROOF_INVALID", "ath")
    if nonce is not None and payload.get("nonce") != nonce:
        raise TokenError("DPOP_NONCE_REQUIRED")
    echoed = payload.get("nonce")
    return DPoPProof(jti, issued, echoed if isinstance(echoed, str) else None)
