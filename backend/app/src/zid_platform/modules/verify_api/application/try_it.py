"""The sandbox "try it" runner (ZID-CP-WF10 v2.0, WF-10.05; CR-173).

A signed-in administrator runs one partner call against ONE SANDBOX application and sees the exact request and answer. It exists so a developer can learn the API without writing a client.

Safety is structural, not a warning:
- only a SANDBOX application can be tried; a production application is refused, so this can never reach production;
- it acts as the application with the scopes the application HOLDS NOW (not the whole list), so a call the application could not make is refused here too;
- only five read-and-create operations are offered, each the same service call the partner route makes; there is no way to name another path, a method or a header;
- it creates only sandbox sessions, which call no provider and cost nothing.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.verify_api.application import sessions_service as svc
from zid_platform.modules.verify_api.application.service import VerifyApiCaller
from zid_platform.modules.verify_api.domain import sessions as sessions_domain
from zid_platform.modules.verify_api.domain.model import SCOPES, PartnerError
from zid_platform.modules.verify_api.infrastructure import store

OPERATIONS: dict[str, dict[str, str]] = {
    "whoami": {"method": "GET", "path": "/v1/verify-api/whoami", "scope": ""},
    "products": {"method": "GET", "path": "/v1/verify-api/products", "scope": "products:read"},
    "workflows": {"method": "GET", "path": "/v1/verify-api/workflows", "scope": "workflows:read"},
    "list_sessions": {"method": "GET", "path": "/v1/verify-api/verification-sessions", "scope": "verifications:list"},
    "create_session": {"method": "POST", "path": "/v1/verify-api/verification-sessions", "scope": "verifications:create"},
}
DEFAULT_BODY = {"customer_reference": "try-it", "country": "US", "purpose_code": "customer_onboarding"}


async def run(conn: AsyncConnection, tenant_id: str, application_id: str, operation: str, *, body: dict[str, Any] | None, idempotency_key: str | None, origin: str, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    op = OPERATIONS.get(operation)
    if op is None:
        raise PartnerError("VALIDATION_FAILED", f"Choose one of: {', '.join(OPERATIONS)}.")
    app = await store.get_application(conn, application_id)
    if app is None:
        raise PartnerError("NOT_FOUND", "No such application.")
    if str(app["environment"]) != "SANDBOX":
        raise PartnerError("ENVIRONMENT_MISMATCH", "Try it runs only against a sandbox application. A production application is never called from here.")
    if str(app["status"]) != "ACTIVE":
        raise PartnerError("APPLICATION_STATE_INVALID", "This application is not active.")
    held = await store.scopes_in_force(conn, application_id, now)
    scopes = tuple(s for s in (held if held is not None else app["scopes"]) if s in SCOPES)
    who = VerifyApiCaller(tenant_id=tenant_id, application_id=application_id, credential_id="try-it", environment="SANDBOX", scopes=scopes, expires_at=now + timedelta(minutes=1))
    if op["scope"]:
        who.require(op["scope"])  # the application could not make this call itself: neither can the runner
    request: dict[str, Any] = {"method": op["method"], "path": op["path"], "headers": {"Authorization": "Bearer <your access token>"}}
    if operation == "whoami":
        out: dict[str, Any] = {"application_id": application_id, "environment": "sandbox", "scopes": list(scopes)}
    elif operation == "products":
        out = {"items": sessions_domain.PRODUCTS}
    elif operation == "workflows":
        out = svc.catalogue(app)
    elif operation == "list_sessions":
        out = await svc.list_sessions(conn, who, limit=10)
    else:
        full = {**DEFAULT_BODY, **(body or {})}
        key = idempotency_key or f"try-it-{application_id[:8]}-{int(now.timestamp())}"
        request["headers"]["Idempotency-Key"] = key
        request["body"] = full
        view, created = await svc.create_session(conn, who, key=key, body=full, origin=origin, now=now)
        out = {**view, "created": created}
    return {"request": request, "status": 201 if operation == "create_session" and out.get("created") else 200, "response": out, "sandbox": True, "note": "This ran against the sandbox. It called no provider and created nothing in production."}
