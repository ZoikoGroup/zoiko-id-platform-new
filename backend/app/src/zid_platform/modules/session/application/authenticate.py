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

from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine
from zid_context import RequestContext
from zid_crypto import CryptoError, KeyRing, jwk_thumbprint, peek_header, verify_jws
from zid_db import tenant_transaction

from zid_platform.modules.authentication.ports import AAL
from zid_platform.modules.configuration.ports import session_limits
from zid_platform.modules.principal.ports import PrincipalStatus, load_principal
from zid_platform.modules.session.domain.session import SessionPolicy, SessionStatus, check
from zid_platform.modules.session.domain.tokens import (
    ACCESS_TOKEN_TYP,
    DPOP_MAX_AGE,
    DPOP_TYP,
    AccessClaims,
    DPoPProof,
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


def verify_dpop_proof(
    dpop: str, *, method: str, url: str, access_token: str | None, now: datetime
) -> tuple[str, DPoPProof]:
    """Signature by the proof's own embedded key, then the claim checks. Returns (jkt, proof).

    The caller compares the thumbprint with the binding it expects and records the jti."""
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
    proof = validate_dpop_claims(
        header, body, method=method, url=url, access_token=access_token, now=now
    )
    return thumbprint, proof


def _is_api_agent_credential(client_id: str, scope: frozenset[str], proof_verified: bool) -> bool:
    """True only for a token this platform minted for an API-based agent (CR-094: `client_id` is `api-agent:<client>`, scope `workload`)
    and only when the request carried a verified DPoP proof of the key the token is bound to. Both facts come from the signed token and
    the verified proof, never from a header the caller sets."""
    return proof_verified and "workload" in scope and client_id.startswith("api-agent:")


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
    client_cert_thumbprint: str | None = None,
) -> Authenticated:
    """`client_cert_thumbprint`: x5t#S256 of the mTLS client certificate the edge forwarded."""
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
        thumbprint, proof = verify_dpop_proof(
            dpop, method=method, url=url, access_token=token, now=now
        )
        if thumbprint != claims.cnf_jkt:
            raise TokenError("DPOP_KEY_MISMATCH")
    elif scheme == "dpop":
        raise TokenError("TOKEN_NOT_SENDER_CONSTRAINED")
    if claims.cnf_x5t is not None:  # RFC 8705 §3: the same certificate must present the token
        if client_cert_thumbprint is None:
            raise TokenError("MTLS_CERTIFICATE_REQUIRED")
        if client_cert_thumbprint != claims.cnf_x5t:
            raise TokenError("MTLS_CERTIFICATE_MISMATCH")

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
            # The tenant may only shorten the platform's session policy, never lengthen it.
            limit_absolute, limit_idle = await session_limits(conn)
            policy = settings.session_policy
            if limit_idle is not None:
                policy = SessionPolicy(policy.absolute_ttl, min(policy.idle_timeout, limit_idle))
            if limit_absolute is not None:
                session = replace(
                    session, expires_at=min(session.expires_at, session.auth_time + limit_absolute)
                )
            result = check(
                session,
                principal_revision=revision,
                principal_active=active,
                now=now,
                policy=policy,
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
        else:  # no identity session (client_credentials): the subject itself must still be active
            try:
                principal, _ = await load_principal(conn, claims.subject)
            except ValueError as e:
                raise TokenError("PRINCIPAL_NOT_ACTIVE") from e
            if principal.status is not PrincipalStatus.ACTIVE:
                raise TokenError("PRINCIPAL_NOT_ACTIVE")
            if _is_api_agent_credential(claims.client_id, claims.scope, proof is not None):
                # CR-094: possession of the agent's own key, proven on THIS request by the DPoP proof, is basic assurance (Z1). That clears
                # the R1 floor the token's risk class allows and nothing above it: R2 needs Z2 and R3 needs Z3, which a key proof never reaches.
                aal, auth_time = AAL.Z1, claims.issued_at
    if failure is not None:  # raised after commit, so the new session state is kept
        raise TokenError(failure)
    bound = proof is not None or claims.cnf_x5t is not None
    return Authenticated(claims, ctx, aal, auth_time, bound)


__all__ = [
    "Authenticated",
    "SessionStatus",
    "TokenError",
    "TokenSettings",
    "authenticate",
    "verify_dpop_proof",
]
