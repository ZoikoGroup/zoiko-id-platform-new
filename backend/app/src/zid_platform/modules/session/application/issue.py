"""Access-token issuance (ZID-AUTH-001 §6.3-6.4; RFC 9068). Used by the token endpoint (AUTH-W2).

Tokens carry identity context only: subject, tenant, client, coarse scopes, session id, auth
context and an optional DPoP key binding. Lifetime is capped by risk class; authority, grants and
mandate balances are never embedded (§6.3 "never embed").
"""

from __future__ import annotations

from datetime import datetime, timedelta

from zid_context import uuid7
from zid_crypto import KeyRing

from zid_platform.modules.session.domain.tokens import ACCESS_TOKEN_TYP, MAX_ACCESS_LIFETIME


def issue_access_token(
    keyring: KeyRing,
    *,
    issuer: str,
    audience: str,
    subject: str,
    tenant_id: str,
    client_id: str,
    now: datetime,
    scope: frozenset[str] = frozenset(),
    session_id: str | None = None,
    acr: str | None = None,
    amr: tuple[str, ...] = (),
    auth_time: datetime | None = None,
    dpop_jkt: str | None = None,
    cert_thumbprint: str | None = None,
    actor: dict[str, object] | None = None,
    risk_class: int = 1,
    lifetime: timedelta | None = None,
) -> str:
    if dpop_jkt and cert_thumbprint:
        raise ValueError("a token is bound to one sender key: DPoP or mTLS, not both")
    cap = MAX_ACCESS_LIFETIME[risk_class]
    ttl = min(lifetime or cap, cap)
    iat = int(now.timestamp())
    claims: dict[str, object] = {
        "iss": issuer,
        "aud": audience,
        "sub": subject,
        "tenant_id": tenant_id,
        "client_id": client_id,
        "scope": " ".join(sorted(scope)),
        "jti": str(uuid7()),
        "iat": iat,
        "nbf": iat,
        "exp": iat + int(ttl.total_seconds()),
        "risk_class": risk_class,
    }
    if session_id:
        claims["sid"] = session_id
    if acr:
        claims["acr"] = acr
    if amr:
        claims["amr"] = list(amr)
    if auth_time:
        claims["auth_time"] = int(auth_time.timestamp())
    if dpop_jkt:
        claims["cnf"] = {"jkt": dpop_jkt}
    elif cert_thumbprint:
        claims["cnf"] = {"x5t#S256": cert_thumbprint}
    if actor:
        claims["act"] = actor
    return keyring.sign(claims, typ=ACCESS_TOKEN_TYP)
