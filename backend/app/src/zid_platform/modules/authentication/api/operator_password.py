"""Email and password sign-in for the Operator Console. Every other sign-in is a passkey.

    POST /authn/operator/password     {client_id, email, password} -> identity session + __Host- cookie

The operators are the entries of ZID_OPERATOR_PASSWORD_LOGINS / ZID_OPERATOR_LOGINS_FILE (email -> principal id and a
scrypt hash, see entrypoints/hash_operator_password.py); with none configured the route does not exist. It signs in
principals of the operator tenant through an operator client only, locks an email for 15 minutes after five wrong
passwords, and records each outcome. The session is recorded at AAL-Z2 with the context `operator:password`, which the
operator gate accepts without the five-minute passkey freshness it asks of any other session (operator.py).
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from zid_context import RequestContext, current_context
from zid_db import tenant_transaction
from zid_errors import ZidError
from zid_outbox import emit

from zid_platform.api_auth import request_engine
from zid_platform.modules.authentication.api.webauthn import _check_origin, _set_cookie, _settings
from zid_platform.modules.authentication.domain.operator_password import (
    DECOY_HASH,
    Lockout,
    verify_password,
)
from zid_platform.modules.configuration.ports import session_expiry
from zid_platform.modules.principal.ports import PrincipalStatus, load_principal
from zid_platform.modules.session.ports import (
    Session,
    SessionStatus,
    client_tenant,
    create_session,
    issue_handle,
)

router = APIRouter(tags=["authentication"], include_in_schema=False)

OPERATOR_PASSWORD_CONTEXT = "operator:password"
_REFUSED = ZidError("AUTHENTICATION_FAILED", "Email or password is incorrect.", http_status=401)


class OperatorPasswordIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_id: str = Field(min_length=1, max_length=128)
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=256)


def _ctx() -> RequestContext:
    return current_context() or RequestContext("req_unknown", "cor_unknown", "zoiko-id", "unknown")


def _lockout(request: Request) -> Lockout:
    state = request.app.state
    if getattr(state, "operator_lockout", None) is None:
        state.operator_lockout = Lockout()
    lockout: Lockout = state.operator_lockout
    return lockout


@router.post("/authn/operator/password")
async def operator_password_sign_in(
    body: OperatorPasswordIn, request: Request, response: Response
) -> dict[str, Any]:
    settings = _settings(request)
    if request.headers.get("origin") not in getattr(request.app.state, "operator_origins", frozenset()):
        _check_origin(request, settings)
    operator = getattr(request.app.state, "operator_settings", None)
    logins: dict[str, dict[str, str]] = getattr(request.app.state, "operator_password_logins", {})
    if operator is None or not operator.tenant_id or body.client_id not in operator.client_ids:
        raise ZidError("OPERATOR_CLIENT_REQUIRED", "Sign in through the Operator Console.", 400)
    tenant = await client_tenant(request_engine(request), body.client_id)
    if tenant is None or tenant != operator.tenant_id:
        raise ZidError("CLIENT_UNKNOWN", "Unknown client.", http_status=400)

    email = body.email.strip().lower()
    lockout, entry, now_s = _lockout(request), logins.get(email), time.monotonic()
    wait = lockout.locked_for(email, now_s) if entry else 0
    if wait:
        raise ZidError(
            "AUTHENTICATION_LOCKED",
            f"Too many wrong passwords. Try again in {(wait + 59) // 60} minutes.",
            http_status=429,
            retryable=True,
            retry_after_ms=wait * 1000,
            headers={"Retry-After": str(wait)},
        )
    right = verify_password(body.password, entry["password_hash"] if entry else DECOY_HASH)
    now = datetime.now(UTC)
    if entry is None or not right:
        if entry is not None:
            lockout.failed(email, now_s)
        await _record_failure(request, tenant, now)
        raise _REFUSED
    principal_id = entry["principal_id"]
    async with tenant_transaction(request_engine(request), _ctx().with_identity(tenant, None)) as c:
        try:
            principal, _ = await load_principal(c, principal_id)
        except Exception as e:  # noqa: BLE001 - a login naming a principal that does not exist is refused like a wrong password
            raise _REFUSED from e
        if principal.status is not PrincipalStatus.ACTIVE:
            raise _REFUSED
        session = Session(
            session_id=str(uuid.uuid4()),
            tenant_id=tenant,
            principal_id=principal_id,
            auth_time=now,
            authentication_context=OPERATOR_PASSWORD_CONTEXT,
            aal=2,
            observed_security_revision=principal.security_revision,
            risk_class=1,
            status=SessionStatus.ACTIVE,
            expires_at=await session_expiry(c, now, settings.session_policy.absolute_ttl),
            last_seen_at=now,
        )
        await create_session(c, session)
        handle = await issue_handle(c, session.session_id)
        await emit(
            c,
            name="authentication.succeeded",
            tenant_id=tenant,
            aggregate_type="authentication",
            aggregate_id=session.session_id,
            aggregate_version=1,
            data={
                "principal_id": principal_id,
                "session_id": session.session_id,
                "method": "operator-password",
                "aal": session.aal,
            },
        )
    lockout.succeeded(email)
    _set_cookie(response, handle, session)
    return {
        "session_id": session.session_id,
        "principal_id": session.principal_id,
        "aal": session.aal,
        "amr": ["pwd"],
        "auth_time": session.auth_time.isoformat(),
        "expires_at": session.expires_at.isoformat(),
        "step_up_recommended": False,
    }


async def _record_failure(request: Request, tenant: str, now: datetime) -> None:
    """authentication.failed with a privacy-safe reason only: no email, no principal."""
    async with tenant_transaction(request_engine(request), _ctx().with_identity(tenant, None)) as c:
        await emit(
            c,
            name="authentication.failed",
            tenant_id=tenant,
            aggregate_type="authentication_attempt",
            aggregate_id=str(uuid.uuid4()),
            aggregate_version=1,
            data={"reason": "OPERATOR_PASSWORD_INVALID", "ceremony": "operator-password", "at": now.isoformat()},
        )
