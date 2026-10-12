"""Resource-server authentication of one API request (ZID-AUTH-001 §6.3, §7.1, §5.1, §16; RFC 9068/9449).

    Authorization: DPoP <at+jwt>   + DPoP: <proof>     sender-constrained (cnf.jkt)
    Authorization: Bearer <at+jwt>                     only for tokens without a key binding

Order of checks: token signature (allowlisted algs, known kid) -> claims (issuer, audience,
lifetime, tenant) -> DPoP proof (same key as cnf.jkt, method, URL, ath, freshness, one-time jti) ->
identity session (exists, same subject, not expired/idle/revoked, security revision unchanged,
principal still active). Any failure is a 401; a bound token is never accepted as a bearer token.
Session state changes found here (expiry, re-auth) are persisted even though the request fails.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine
from zid_context import RequestContext
from zid_crypto import CryptoError, KeyRing, jwk_thumbprint, peek_header, verify_jws
from zid_db import tenant_transaction

from zid_platform.modules.authentication.ports import AAL
from zid_platform.modules.principal.ports import PrincipalStatus, load_principal
from zid_platform.modules.session.domain.session import SessionPolicy, SessionStatus, check
from zid_platform.modules.session.domain.tokens import (
    ACCESS_TOKEN_TYP,
    DPOP_MAX_AGE,
    DPOP_TYP,
    AccessClaims,
    TokenError,
    validate_access_claims,
    validate_dpop_claims,
)
from zid_platform.modules.session.infrastructure import session_store


@dataclass(frozen=True, slots=True)
class TokenSettings:
    issuer: str
    audience: str
    keyring: KeyRing  # verification keys of the issuer (active + rotation window)
    session_policy: SessionPolicy = field(default_factory=SessionPolicy)


@dataclass(frozen=True, slots=True)
class Authenticated:
    claims: AccessClaims
    ctx: RequestContext
    aal: AAL  # base level of the identity session (Z0 when no session is bound)
    auth_time: datetime | None
    sender_constrained: bool


def _split(authorization: str) -> tuple[str, str]:
    scheme, _, token = authorization.strip().partition(" ")
    if scheme.lower() not in ("bearer", "dpop") or not token or " " in token.strip():
        raise TokenError("TOKEN_MISSING")
    return scheme.lower(), token.strip()


def _verify(
    token: str, *, keys: list[dict[str, Any]], typ: str, code: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        return verify_jws(token, keys=keys, typ=typ)
    except CryptoError as e:
        raise TokenError(code, e.code) from e


async def authenticate(
    engine: AsyncEngine,
    settings: TokenSettings,
    *,
    authorization: str | None,
    dpop: str | None,
    method: str,
    url: str,
    base_ctx: RequestContext,
    now: datetime,
) -> Authenticated:
    if not authorization:
        raise TokenError("TOKEN_MISSING")
    scheme, token = _split(authorization)
    _, payload = _verify(
        token, keys=settings.keyring.jwks()["keys"], typ=ACCESS_TOKEN_TYP, code="TOKEN_INVALID"
    )
    claims = validate_access_claims(
        payload, issuer=settings.issuer, audience=settings.audience, now=now
    )

    proof = None
    if claims.cnf_jkt is not None:
        if scheme != "dpop" or not dpop:
            raise TokenError("DPOP_PROOF_REQUIRED")  # never downgrade a bound token to bearer
        try:
            jwk = peek_header(dpop).get("jwk")
        except CryptoError as e:
            raise TokenError("DPOP_PROOF_INVALID", e.code) from e
        if not isinstance(jwk, dict):
            raise TokenError("DPOP_PROOF_INVALID", "jwk")
        header, body = _verify(dpop, keys=[jwk], typ=DPOP_TYP, code="DPOP_PROOF_INVALID")
        try:
            thumbprint = jwk_thumbprint(jwk)
        except CryptoError as e:
            raise TokenError("DPOP_PROOF_INVALID", e.code) from e
        if thumbprint != claims.cnf_jkt:
            raise TokenError("DPOP_KEY_MISMATCH")
        proof = validate_dpop_claims(
            header, body, method=method, url=url, access_token=token, now=now
        )
    elif scheme == "dpop":
        raise TokenError("TOKEN_NOT_SENDER_CONSTRAINED")

    ctx = base_ctx.with_identity(claims.tenant_id, claims.subject)
    failure: str | None = None
    aal, auth_time = AAL.Z0, None
    async with tenant_transaction(engine, ctx) as conn:
        if proof is not None and not await session_store.record_dpop_proof(
            conn,
            tenant_id=claims.tenant_id,
            jkt=claims.cnf_jkt or "",
            jti=proof.jti,
            issued_at=proof.issued_at,
            window=DPOP_MAX_AGE * 2,
        ):
            raise TokenError("DPOP_PROOF_REPLAYED")
        if claims.session_id is not None:
            session = await session_store.load_session(conn, claims.session_id)
            if session is None or session.principal_id != claims.subject:
                raise TokenError("SESSION_INVALID")
            try:
                principal, _ = await load_principal(conn, claims.subject)
                revision, active = (
                    principal.security_revision,
                    principal.status is PrincipalStatus.ACTIVE,
                )
            except ValueError:
                revision, active = -1, False
            result = check(
                session,
                principal_revision=revision,
                principal_active=active,
                now=now,
                policy=settings.session_policy,
            )
            if result.next_status is not None:
                await session_store.set_status(
                    conn, session.session_id, result.next_status, reason=result.code or ""
                )
            if result.ok:
                await session_store.touch(conn, session.session_id, now)
                aal, auth_time = AAL(session.aal), session.auth_time
            else:
                failure = result.code
    if failure is not None:  # raised after commit, so the new session state is kept
        raise TokenError(failure)
    return Authenticated(claims, ctx, aal, auth_time, proof is not None)


__all__ = ["Authenticated", "SessionStatus", "TokenError", "TokenSettings", "authenticate"]
