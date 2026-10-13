"""Shared API plumbing for module routers: the authenticated caller and domain-error mapping.

`authenticated_caller` is the single authentication seam (ZID-AUTH-001 §6.3, §7.1): it verifies the
access token (and DPoP proof) and the identity session, and derives tenant and principal from them
only (INV-13). Without a configured token issuer every protected endpoint answers 401. Domain error
codes map to HTTP statuses per ZID-API-001 §3.3.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from fastapi import Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine
from zid_context import RequestContext, current_context
from zid_db import tenant_transaction
from zid_errors import ZidError

from zid_platform.modules.authentication.ports import effective_level
from zid_platform.modules.decision.domain.pdp import AuthnFact
from zid_platform.modules.session.ports import TokenError, TokenSettings, authenticate
from zid_platform.modules.signals.ports import security_view

# The risk class the shared authentication seam reads security state for. Protected (R2/R3)
# actions re-read the committed state in their own transaction (protected.py), never this one.
CALLER_STATE_RISK_CLASS = 1


@dataclass(frozen=True, slots=True)
class Caller:
    ctx: RequestContext  # server-derived tenant and the authenticated principal
    authn: AuthnFact
    security_state: str = "UNKNOWN"  # from the signals PIP; UNKNOWN fails closed for R2/R3
    client_id: str = "unknown-client"  # OAuth client, scopes idempotency keys
    scope: frozenset[str] = frozenset()  # coarse OAuth scopes of the access token
    actor: str | None = None  # RFC 8693 actor when the token came from a token exchange
    security_revision: int = 0  # the security-state revision `security_state` was read at
    session_auth_time: datetime | None = None  # as asserted (None: token without auth_time)


def client_cert_thumbprint(request: Request) -> str | None:
    """x5t#S256 of the client certificate the TLS terminator forwarded, when mTLS is configured.

    The header is trusted only because the deployment names it and the edge strips it inbound."""
    header: str | None = getattr(request.app.state, "mtls_cert_header", None)
    if not header:
        return None
    from zid_platform.modules.session.ports import cert_thumbprint, parse_client_cert

    try:
        cert = parse_client_cert(request.headers.get(header))
    except ValueError:
        return None
    return cert_thumbprint(cert) if cert is not None else None


def _challenge(scheme: str, code: str | None) -> dict[str, str]:
    error = "" if code in (None, "TOKEN_MISSING") else ', error="invalid_token"'
    if code == "DPOP_NONCE_REQUIRED":
        error = ', error="use_dpop_nonce"'
    extra = ', algs="ES256 Ed25519"' if scheme == "DPoP" else ""
    return {"WWW-Authenticate": f'{scheme} realm="zoiko-id"{error}{extra}'}


async def authenticated_caller(request: Request) -> Caller:
    settings: TokenSettings | None = getattr(request.app.state, "token_settings", None)
    engine: AsyncEngine | None = getattr(request.app.state, "engine", None)
    authorization = request.headers.get("authorization")
    scheme = (
        "DPoP"
        if (authorization or "").lower().startswith("dpop ") or request.headers.get("dpop")
        else "Bearer"
    )
    if settings is None or engine is None:
        raise ZidError(
            "UNAUTHENTICATED",
            "A trusted, authenticated caller is required.",
            http_status=401,
            headers=_challenge(scheme, None),
        )
    base = getattr(request.app.state, "public_base_url", None)
    url = (
        (base.rstrip("/") + request.url.path) if base else str(request.url)
    )  # canonical external URL
    ctx = current_context() or RequestContext("req_unknown", "cor_unknown", "zoiko-id", "unknown")
    now = datetime.now(UTC)
    try:
        auth = await authenticate(
            engine,
            settings,
            authorization=authorization,
            dpop=request.headers.get("dpop"),
            method=request.method,
            url=url,
            base_ctx=ctx,
            now=now,
            client_cert_thumbprint=client_cert_thumbprint(request),
        )
    except TokenError as e:
        code = "UNAUTHENTICATED" if e.code == "TOKEN_MISSING" else e.code
        raise ZidError(
            code, "Authentication failed.", http_status=401, headers=_challenge(scheme, e.code)
        ) from e
    security_state, security_revision = "UNKNOWN", 0  # a failed read stays UNKNOWN
    try:
        view = await _security_view(engine, auth.ctx, auth.claims.subject, auth.auth_time, now)
        security_state, security_revision = view.state, view.revision
    except Exception:  # noqa: BLE001 - the PIP is optional for R0/R1, mandatory (fail closed) above
        security_state = "UNKNOWN"
    auth_time = auth.auth_time or auth.claims.issued_at
    level = (
        effective_level(auth.aal, auth_time=auth.auth_time, now=now)  # Z3 = Z2 + freshness
        if auth.auth_time is not None
        else auth.aal
    )
    return Caller(
        auth.ctx,
        AuthnFact(int(level), auth_time),
        security_state=security_state,
        client_id=auth.claims.client_id,
        scope=auth.claims.scope,
        actor=auth.claims.actor,
        security_revision=security_revision,
        session_auth_time=auth.auth_time,
    )


async def _security_view(
    engine: AsyncEngine,
    ctx: RequestContext,
    principal_id: str,
    auth_time: datetime | None,
    now: datetime,
) -> Any:
    """Cache first (bounded by CALLER_STATE_RISK_CLASS), else one read in its own transaction."""
    tenant = ctx.tenant_id or ""
    try:
        return await security_view(
            None,
            tenant_id=tenant,
            principal_id=principal_id,
            auth_time=auth_time,
            risk_class=CALLER_STATE_RISK_CLASS,
            now=now,
        )
    except LookupError:
        async with tenant_transaction(engine, ctx) as conn:
            return await security_view(
                conn,
                tenant_id=tenant,
                principal_id=principal_id,
                auth_time=auth_time,
                risk_class=CALLER_STATE_RISK_CLASS,
                now=now,
            )


_FORBIDDEN = (
    "AUTHORITY_",
    "GRANTOR_",
    "GRANT_",
    "ASSURANCE_INSUFFICIENT",
    "MANDATE_SOURCE_INVALID",
    "MANDATE_SCOPE_EXCEEDED",
    "MANDATE_TOOL_NOT_ALLOWED",
    "MANDATE_EXECUTOR_MISMATCH",
    "PURPOSE_NOT_ALLOWED",
    "APPROVER_",
)
_UNPROCESSABLE = (
    "MANDATE_VALUE_INVALID",
    "MANDATE_CURRENCY_MISMATCH",
    "VALIDATION_FAILED",
    "MANDATE_BUDGET_INVALID",
)


def http_error(code: str, detail: str = "") -> ZidError:
    """Domain code -> ZidError with the API §3.3 status (codes stay stable machine contracts)."""
    if code.endswith("NOT_FOUND"):
        status = 404
    elif code.startswith(_UNPROCESSABLE):
        status = 422
    elif code.startswith(_FORBIDDEN):
        status = 403
    elif code in ("STATE_UNAVAILABLE", "DEPENDENCY_UNAVAILABLE"):
        status = 503
    else:
        status = 409  # limits, conflicts, stale writer epoch, invalid transitions
    retryable = code in ("WRITER_EPOCH_STALE", "STATE_UNAVAILABLE", "CONCURRENT_MODIFICATION")
    return ZidError(
        code,
        detail or code.replace("_", " ").capitalize() + ".",
        http_status=status,
        retryable=retryable,
        retry_after_ms=250 if retryable else None,
    )


def request_engine(request: Request) -> AsyncEngine:
    engine: AsyncEngine | None = getattr(request.app.state, "engine", None)
    if engine is None:
        raise http_error("DEPENDENCY_UNAVAILABLE", "No database is configured for this service.")
    return engine


async def cell_writer_epoch(
    request: Request, conn: AsyncConnection, tenant_id: str
) -> tuple[uuid.UUID, int]:
    """This cell's identity and the tenant's current writer epoch (DEC-C5; MAND-21).

    Ledger writes are accepted only in the cell that holds the tenant's writer fence; a request routed
    to any other cell is refused (retryable after re-routing) instead of writing under a stale epoch.
    The ledger re-checks the fence under a row lock in the same transaction.
    """
    cell: str | None = getattr(request.app.state, "cell_uuid", None)
    if not cell:
        raise http_error("DEPENDENCY_UNAVAILABLE", "This cell has no ZID_CELL_UUID configured.")
    row = (
        await conn.execute(
            text("SELECT cell_id, epoch FROM agent.writer_fence WHERE tenant_id = :t"),
            {"t": uuid.UUID(tenant_id)},
        )
    ).first()
    if row is None or str(row.cell_id) != cell:
        raise http_error("WRITER_EPOCH_STALE", "This cell is not the tenant's current writer.")
    return uuid.UUID(cell), int(row.epoch)
