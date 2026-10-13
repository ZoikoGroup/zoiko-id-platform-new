"""OAuth clients, authorization codes, refresh-token families and DPoP nonces in PostgreSQL.

ZID-AUTH-001 §6, §7.1, §8; ZID-DATA-001 §5.3. Everything except `client_tenant` runs inside a
tenant transaction (RLS). Only SHA-256 hashes of codes and refresh tokens are stored; each
single-use row is claimed with a conditional UPDATE, so two concurrent redemptions cannot both win.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine
from zid_outbox import emit

from zid_platform.modules.session.domain.oauth import (
    PAR_URI_PREFIX,
    OAuthClient,
    opaque_token,
    token_hash,
)

_CLIENT_COLS = (
    "client_id, tenant_id, client_type, profile, redirect_uris, grant_types, scopes, audiences, "
    "token_auth_method, dpop_bound, status, first_party, require_dpop_nonce, refresh_ttl_seconds, "
    "jwks, tls_subject_dn, service_principal_id, require_par"
)


async def client_tenant(engine: AsyncEngine, client_id: str) -> str | None:
    """The tenant a client is registered in: the only lookup made before a tenant is known."""
    if not client_id or len(client_id) > 256:
        return None
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT tenant_id FROM authn.client_directory WHERE client_id = :c"),
                {"c": client_id},
            )
        ).first()
    return str(row.tenant_id) if row else None


async def load_client(conn: AsyncConnection, client_id: str) -> OAuthClient | None:
    row = (
        await conn.execute(
            text(f"SELECT {_CLIENT_COLS} FROM authn.oauth_client WHERE client_id = :c"),
            {"c": client_id},
        )
    ).first()
    if row is None:
        return None
    m = row._mapping
    return OAuthClient(
        client_id=m["client_id"],
        tenant_id=str(m["tenant_id"]),
        client_type=m["client_type"],
        profile=m["profile"],
        redirect_uris=tuple(m["redirect_uris"]),
        grant_types=frozenset(m["grant_types"]),
        scopes=frozenset(m["scopes"]),
        audiences=tuple(m["audiences"]),
        token_auth_method=m["token_auth_method"],
        dpop_bound=m["dpop_bound"],
        status=m["status"],
        first_party=m["first_party"],
        require_dpop_nonce=m["require_dpop_nonce"],
        refresh_ttl=timedelta(seconds=m["refresh_ttl_seconds"]),
        jwks=m["jwks"],
        tls_subject_dn=m["tls_subject_dn"],
        service_principal_id=str(m["service_principal_id"]) if m["service_principal_id"] else None,
        require_par=m["require_par"],
    )


async def create_client(conn: AsyncConnection, c: OAuthClient) -> None:
    await conn.execute(
        text(
            f"INSERT INTO authn.oauth_client ({_CLIENT_COLS}) VALUES (:id, :t, :type, :profile, "
            ":redirects, :grants, :scopes, :aud, :auth, :dpop, :status, :fp, :nonce, :ttl, "
            "CAST(:jwks AS jsonb), :dn, :sp, :par)"
        ),
        {
            "id": c.client_id,
            "t": uuid.UUID(c.tenant_id),
            "type": c.client_type,
            "profile": c.profile,
            "redirects": list(c.redirect_uris),
            "grants": sorted(c.grant_types),
            "scopes": sorted(c.scopes),
            "aud": list(c.audiences),
            "auth": c.token_auth_method,
            "dpop": c.dpop_bound,
            "status": c.status,
            "fp": c.first_party,
            "nonce": c.require_dpop_nonce,
            "ttl": int(c.refresh_ttl.total_seconds()),
            "jwks": json.dumps(c.jwks) if c.jwks is not None else None,
            "dn": c.tls_subject_dn,
            "sp": uuid.UUID(c.service_principal_id) if c.service_principal_id else None,
            "par": c.require_par,
        },
    )


@dataclass(frozen=True, slots=True)
class CodeGrant:
    client_id: str
    principal_id: str
    session_id: str
    redirect_uri: str
    scope: frozenset[str]
    audience: str
    code_challenge: str
    nonce: str | None
    dpop_jkt: str | None
    expires_at: datetime
    used: bool
    family_id: str | None


async def save_code(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    client_id: str,
    principal_id: str,
    session_id: str,
    redirect_uri: str,
    scope: frozenset[str],
    audience: str,
    code_challenge: str,
    nonce: str | None,
    dpop_jkt: str | None,
    expires_at: datetime,
) -> str:
    code = opaque_token()
    await conn.execute(
        text(
            "INSERT INTO authn.authorization_code (code_hash, tenant_id, client_id, principal_id, "
            "session_id, redirect_uri, scope, audience, code_challenge, nonce, dpop_jkt, expires_at) "
            "VALUES (:h, :t, :c, :p, :s, :r, :scope, :aud, :ch, :n, :j, :exp)"
        ),
        {
            "h": token_hash(code),
            "t": uuid.UUID(tenant_id),
            "c": client_id,
            "p": uuid.UUID(principal_id),
            "s": uuid.UUID(session_id),
            "r": redirect_uri,
            "scope": sorted(scope),
            "aud": audience,
            "ch": code_challenge,
            "n": nonce,
            "j": dpop_jkt,
            "exp": expires_at,
        },
    )
    return code


async def claim_code(conn: AsyncConnection, code: str, now: datetime) -> CodeGrant | None:
    """Mark the code used and return it as it was; a replay returns it with used=True."""
    row = (
        await conn.execute(
            text(
                "SELECT client_id, principal_id, session_id, redirect_uri, scope, audience, "
                "code_challenge, nonce, dpop_jkt, expires_at, used_at, family_id "
                "FROM authn.authorization_code WHERE code_hash = :h FOR UPDATE"
            ),
            {"h": token_hash(code)},
        )
    ).first()
    if row is None:
        return None
    if row.used_at is None:
        await conn.execute(
            text("UPDATE authn.authorization_code SET used_at = :now WHERE code_hash = :h"),
            {"h": token_hash(code), "now": now},
        )
    return CodeGrant(
        client_id=row.client_id,
        principal_id=str(row.principal_id),
        session_id=str(row.session_id),
        redirect_uri=row.redirect_uri,
        scope=frozenset(row.scope),
        audience=row.audience,
        code_challenge=row.code_challenge,
        nonce=row.nonce,
        dpop_jkt=row.dpop_jkt,
        expires_at=row.expires_at,
        used=row.used_at is not None,
        family_id=str(row.family_id) if row.family_id else None,
    )


async def link_code_family(conn: AsyncConnection, code: str, family_id: str) -> None:
    await conn.execute(
        text("UPDATE authn.authorization_code SET family_id = :f WHERE code_hash = :h"),
        {"h": token_hash(code), "f": uuid.UUID(family_id)},
    )


@dataclass(frozen=True, slots=True)
class RefreshState:
    family_id: str
    client_id: str
    principal_id: str
    session_id: str | None
    sender_jkt: str | None
    family_status: str
    family_expires_at: datetime
    scope: frozenset[str]
    audience: str | None
    generation: int
    current_generation: int
    token_status: str


async def create_family(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    client_id: str,
    principal_id: str,
    session_id: str | None,
    sender_jkt: str | None,
    scope: frozenset[str],
    audience: str,
    expires_at: datetime,
    now: datetime,
) -> tuple[str, str]:
    """New family with generation 0. Returns (family_id, refresh_token)."""
    family_id = str(uuid.uuid4())
    await conn.execute(
        text(
            "INSERT INTO authn.refresh_token_family (family_id, tenant_id, client_id, principal_id, "
            "session_id, sender_key_thumbprint, scope, audience, expires_at) "
            "VALUES (:f, :t, :c, :p, :s, :j, :scope, :aud, :exp)"
        ),
        {
            "f": uuid.UUID(family_id),
            "t": uuid.UUID(tenant_id),
            "c": client_id,
            "p": uuid.UUID(principal_id),
            "s": uuid.UUID(session_id) if session_id else None,
            "j": sender_jkt,
            "scope": sorted(scope),
            "aud": audience,
            "exp": expires_at,
        },
    )
    return family_id, await _insert_token(conn, tenant_id, family_id, 0, now)


async def _insert_token(
    conn: AsyncConnection, tenant_id: str, family_id: str, generation: int, now: datetime
) -> str:
    token = opaque_token()
    await conn.execute(
        text(
            "INSERT INTO authn.refresh_token (token_hash, tenant_id, family_id, generation, issued_at) "
            "VALUES (:h, :t, :f, :g, :now)"
        ),
        {
            "h": token_hash(token),
            "t": uuid.UUID(tenant_id),
            "f": uuid.UUID(family_id),
            "g": generation,
            "now": now,
        },
    )
    return token


async def load_refresh(conn: AsyncConnection, token: str) -> RefreshState | None:
    """The presented token and its family, family row locked for the rotation decision."""
    row = (
        await conn.execute(
            text(
                "SELECT f.family_id, f.client_id, f.principal_id, f.session_id, "
                "f.sender_key_thumbprint, f.status AS family_status, f.expires_at, f.scope, "
                "f.audience, f.current_generation, t.generation, t.status AS token_status "
                "FROM authn.refresh_token t JOIN authn.refresh_token_family f USING (family_id) "
                "WHERE t.token_hash = :h FOR UPDATE OF f"
            ),
            {"h": token_hash(token)},
        )
    ).first()
    if row is None:
        return None
    return RefreshState(
        family_id=str(row.family_id),
        client_id=row.client_id,
        principal_id=str(row.principal_id),
        session_id=str(row.session_id) if row.session_id else None,
        sender_jkt=row.sender_key_thumbprint,
        family_status=row.family_status,
        family_expires_at=row.expires_at,
        scope=frozenset(row.scope),
        audience=row.audience,
        generation=row.generation,
        current_generation=row.current_generation,
        token_status=row.token_status,
    )


async def rotate(
    conn: AsyncConnection, *, tenant_id: str, state: RefreshState, token: str, now: datetime
) -> str | None:
    """Spend the presented generation and mint the next. None if it was already spent."""
    spent = await conn.execute(
        text(
            "UPDATE authn.refresh_token SET status = 'USED', used_at = :now "
            "WHERE token_hash = :h AND status = 'ACTIVE'"
        ),
        {"h": token_hash(token), "now": now},
    )
    nxt = state.current_generation + 1
    moved = await conn.execute(
        text(
            "UPDATE authn.refresh_token_family SET current_generation = :n "
            "WHERE family_id = :f AND current_generation = :c AND status = 'ACTIVE'"
        ),
        {"f": uuid.UUID(state.family_id), "n": nxt, "c": state.current_generation},
    )
    if spent.rowcount != 1 or moved.rowcount != 1:
        return None
    return await _insert_token(conn, tenant_id, state.family_id, nxt, now)


async def end_family(
    conn: AsyncConnection, family_id: str, *, status: str, reason: str, now: datetime
) -> bool:
    """REVOKED or EXPIRED; every live token of the family ends with it. False if already ended."""
    ended = await conn.execute(
        text(
            "UPDATE authn.refresh_token_family SET status = :st, revoked_reason = :r, "
            "revoked_at = :now WHERE family_id = :f AND status = 'ACTIVE'"
        ),
        {"f": uuid.UUID(family_id), "st": status, "r": reason, "now": now},
    )
    await conn.execute(
        text(
            "UPDATE authn.refresh_token SET status = :st WHERE family_id = :f AND status = 'ACTIVE'"
        ),
        {"f": uuid.UUID(family_id), "st": status},
    )
    return ended.rowcount == 1


async def report_reuse(conn: AsyncConnection, *, tenant_id: str, state: RefreshState) -> None:
    """refresh_token.reuse_detected: probable theft (ZID-AUTH-001 §8, §23). Ids only, no token."""
    await emit(
        conn,
        name="refresh_token.reuse_detected",
        tenant_id=tenant_id,
        aggregate_type="refresh_token_family",
        aggregate_id=state.family_id,
        aggregate_version=1,
        data={
            "family_id": state.family_id,
            "client_id": state.client_id,
            "principal_id": state.principal_id,
            "session_id": state.session_id,
            "generation": state.generation,
        },
    )


async def issue_nonce(conn: AsyncConnection, *, tenant_id: str, expires_at: datetime) -> str:
    nonce = opaque_token()
    await conn.execute(
        text("INSERT INTO authn.dpop_nonce (tenant_id, nonce, expires_at) VALUES (:t, :n, :exp)"),
        {"t": uuid.UUID(tenant_id), "n": nonce, "exp": expires_at},
    )
    return nonce


async def nonce_valid(conn: AsyncConnection, nonce: object, now: datetime) -> bool:
    if not isinstance(nonce, str) or not 16 <= len(nonce) <= 128:
        return False
    row = (
        await conn.execute(
            text("SELECT 1 FROM authn.dpop_nonce WHERE nonce = :n AND expires_at > :now"),
            {"n": nonce, "now": now},
        )
    ).first()
    return row is not None


async def save_par(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    client_id: str,
    params: dict[str, str],
    dpop_jkt: str | None,
    expires_at: datetime,
) -> str:
    """RFC 9126: the validated request, retrievable once by its request_uri."""
    request_uri = PAR_URI_PREFIX + opaque_token()
    await conn.execute(
        text(
            "INSERT INTO authn.par_request (request_uri_hash, tenant_id, client_id, params, "
            "dpop_jkt, expires_at) VALUES (:h, :t, :c, CAST(:p AS jsonb), :j, :exp)"
        ),
        {
            "h": token_hash(request_uri),
            "t": uuid.UUID(tenant_id),
            "c": client_id,
            "p": json.dumps(params),
            "j": dpop_jkt,
            "exp": expires_at,
        },
    )
    return request_uri


async def claim_par(
    conn: AsyncConnection, request_uri: str, *, client_id: str, now: datetime
) -> tuple[dict[str, str], str | None] | None:
    row = (
        await conn.execute(
            text(
                "UPDATE authn.par_request SET used_at = :now WHERE request_uri_hash = :h "
                "AND client_id = :c AND used_at IS NULL AND expires_at > :now "
                "RETURNING params, dpop_jkt"
            ),
            {"h": token_hash(request_uri), "c": client_id, "now": now},
        )
    ).first()
    if row is None:
        return None
    return dict(row.params), row.dpop_jkt


async def consent_covers(
    conn: AsyncConnection, principal_id: str, client_id: str, scopes: frozenset[str]
) -> bool:
    row = (
        await conn.execute(
            text(
                "SELECT scopes FROM authn.oauth_consent WHERE principal_id = :p AND client_id = :c "
                "AND revoked_at IS NULL"
            ),
            {"p": uuid.UUID(principal_id), "c": client_id},
        )
    ).first()
    return row is not None and scopes <= set(row.scopes)


async def grant_consent(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    principal_id: str,
    client_id: str,
    scopes: frozenset[str],
    now: datetime,
) -> None:
    """Widening is a new consent; the previous one stays as history (revoked)."""
    old = (
        await conn.execute(
            text(
                "UPDATE authn.oauth_consent SET revoked_at = :now WHERE principal_id = :p "
                "AND client_id = :c AND revoked_at IS NULL RETURNING scopes"
            ),
            {"p": uuid.UUID(principal_id), "c": client_id, "now": now},
        )
    ).first()
    merged = sorted(scopes | set(old.scopes if old else ()))
    await conn.execute(
        text(
            "INSERT INTO authn.oauth_consent (consent_id, tenant_id, principal_id, client_id, "
            "scopes, granted_at) VALUES (:id, :t, :p, :c, :s, :now)"
        ),
        {
            "id": uuid.uuid4(),
            "t": uuid.UUID(tenant_id),
            "p": uuid.UUID(principal_id),
            "c": client_id,
            "s": merged,
            "now": now,
        },
    )


async def list_consents(conn: AsyncConnection, principal_id: str) -> list[dict[str, object]]:
    rows = (
        await conn.execute(
            text(
                "SELECT client_id, scopes, granted_at FROM authn.oauth_consent WHERE principal_id = :p "
                "AND revoked_at IS NULL ORDER BY granted_at"
            ),
            {"p": uuid.UUID(principal_id)},
        )
    ).all()
    return [
        {"client_id": r.client_id, "scopes": list(r.scopes), "granted_at": r.granted_at.isoformat()}
        for r in rows
    ]


async def revoke_consent(
    conn: AsyncConnection, principal_id: str, client_id: str, now: datetime
) -> int:
    """Consent withdrawn: the client's refresh families for this principal end with it."""
    result = await conn.execute(
        text(
            "UPDATE authn.oauth_consent SET revoked_at = :now WHERE principal_id = :p AND client_id = :c "
            "AND revoked_at IS NULL"
        ),
        {"p": uuid.UUID(principal_id), "c": client_id, "now": now},
    )
    families = (
        await conn.execute(
            text(
                "SELECT family_id FROM authn.refresh_token_family WHERE principal_id = :p "
                "AND client_id = :c AND status = 'ACTIVE'"
            ),
            {"p": uuid.UUID(principal_id), "c": client_id},
        )
    ).all()
    for f in families:
        await end_family(
            conn, str(f.family_id), status="REVOKED", reason="consent_revoked", now=now
        )
    return result.rowcount


async def open_consent_request(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    principal_id: str,
    session_id: str,
    client_id: str,
    scopes: frozenset[str],
    params: dict[str, str],
    pushed: bool,
    expires_at: datetime,
) -> str:
    request_id = opaque_token()
    await conn.execute(
        text(
            "INSERT INTO authn.consent_request (request_hash, tenant_id, principal_id, session_id, "
            "client_id, scopes, params, pushed, expires_at) VALUES (:h, :t, :p, :s, :c, :sc, "
            "CAST(:params AS jsonb), :pushed, :exp)"
        ),
        {
            "h": token_hash(request_id),
            "t": uuid.UUID(tenant_id),
            "p": uuid.UUID(principal_id),
            "s": uuid.UUID(session_id),
            "c": client_id,
            "sc": sorted(scopes),
            "params": json.dumps(params),
            "pushed": pushed,
            "exp": expires_at,
        },
    )
    return f"{tenant_id}.{request_id}"


async def peek_consent_request(
    conn: AsyncConnection, request_id: str, now: datetime
) -> dict[str, Any] | None:
    row = (
        await conn.execute(
            text(
                "SELECT principal_id, session_id, client_id, scopes, params, pushed, expires_at "
                "FROM authn.consent_request WHERE request_hash = :h AND used_at IS NULL "
                "AND expires_at > :now"
            ),
            {"h": token_hash(request_id), "now": now},
        )
    ).first()
    return dict(row._mapping) if row else None


async def claim_consent_request(
    conn: AsyncConnection, request_id: str, now: datetime
) -> dict[str, Any] | None:
    row = (
        await conn.execute(
            text(
                "UPDATE authn.consent_request SET used_at = :now WHERE request_hash = :h "
                "AND used_at IS NULL AND expires_at > :now RETURNING principal_id, session_id, "
                "client_id, scopes, params, pushed"
            ),
            {"h": token_hash(request_id), "now": now},
        )
    ).first()
    return dict(row._mapping) if row else None
