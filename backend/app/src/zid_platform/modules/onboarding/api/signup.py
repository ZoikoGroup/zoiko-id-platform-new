"""Public company sign-up and workspace lookup (ZID-OVERLAY-001 §4; TENANCY-001 §14; G-W0).

    POST /v1/signup                       create a company tenant; you become its pending OWNER
    GET  /v1/workspaces/{workspace}       workspace name -> console client (to sign in there)

Unauthenticated by design: these are the only way in for someone who has no account yet. Sign-up
is off unless ZID_TENANT_SIGNUP_ENABLED is set; it is rate limited per client address, and it
returns a single-use passkey enrollment ticket for the new company's first principal (shown once).
The new OWNER becomes ACTIVE at the organization gate - in local/test with
ZID_TENANT_SIGNUP_DEV_VERIFY, a synthetic, self-labelled development verification instead.
Nothing about the person is stored: the passkey is the account.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field
from zid_db import in_tenant
from zid_errors import ZidError

from zid_platform.api_auth import request_engine
from zid_platform.modules.onboarding.application.organization import development_verification
from zid_platform.modules.onboarding.domain.gates import OnboardingError
from zid_platform.modules.tenant.ports import ConsoleClient, TenantError, self_signup, workspace
from zid_platform.protected import catalog

router = APIRouter(tags=["signup"])

WINDOW_S, MAX_PER_WINDOW = 600.0, 5
_recent: dict[str, deque[float]] = defaultdict(deque)


def _rate_limit(request: Request) -> None:
    """In-process per-address bound (the edge rate limiter is the production control, DEC-G1)."""
    key = request.client.host if request.client else "unknown"
    now = time.monotonic()
    q = _recent[key]
    while q and now - q[0] > WINDOW_S:
        q.popleft()
    if len(q) >= MAX_PER_WINDOW:
        raise ZidError(
            "RATE_LIMITED",
            "Too many sign-ups from this address; try again later.",
            http_status=429,
            retryable=True,
            retry_after_ms=60_000,
        )
    q.append(now)


class SignupBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    company_name: str = Field(min_length=1, max_length=200)
    workspace: str = Field(
        min_length=1, max_length=63, pattern=r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$"
    )


@router.post("/v1/signup", status_code=201)
async def signup(body: SignupBody, request: Request) -> dict[str, Any]:
    console = getattr(request.app.state, "tenant_console", None)
    if not getattr(request.app.state, "tenant_signup", False) or console is None:
        raise ZidError("SIGNUP_DISABLED", "Company sign-up is not enabled here.", http_status=403)
    _rate_limit(request)
    dev_verify = bool(getattr(request.app.state, "tenant_signup_dev_verify", False))
    environment = str(getattr(request.app.state, "environment", "production"))
    async with request_engine(request).begin() as conn:
        try:
            result = await self_signup(
                conn,
                display_name=body.company_name,
                tenant_slug=body.workspace,
                deployment_mode="O0",
                home_cell_id=getattr(request.app.state, "home_cell", None) or "local-1",
                console=ConsoleClient(*console),
            )
        except TenantError as e:
            code = 409 if e.code == "TENANT_SLUG_TAKEN" else 422
            raise ZidError(e.code, e.detail or "Sign-up failed.", http_status=code) from e
        status = result.tenant.status.value
        verified = False
        if dev_verify:
            async with in_tenant(conn, result.tenant.tenant_id):
                try:
                    outcome = await development_verification(
                        conn,
                        result.tenant.tenant_id,
                        principal_id=result.owner_principal_id,
                        legal_name=body.company_name,
                        catalog=catalog(),
                        environment=environment,
                    )
                except OnboardingError as e:
                    raise ZidError(e.code, e.detail, http_status=409) from e
                status, verified = outcome.status, True
    return {
        "workspace": body.workspace,
        "display_name": result.tenant.display_name,
        "tenant_id": result.tenant.tenant_id,
        "tenant_status": status,
        "console_client_id": result.console_client_id,
        "enrollment_ticket": result.enrollment_ticket,  # shown once
        "enrollment_ticket_expires_at": result.ticket_expires_at.isoformat(),
        "organization": "DEVELOPMENT_UNVERIFIED" if verified else "VERIFICATION_REQUIRED",
    }


@router.get("/v1/workspaces/{name}")
async def lookup(name: str, request: Request) -> dict[str, Any]:
    if not 1 <= len(name) <= 63:
        raise ZidError("WORKSPACE_NOT_FOUND", "No such workspace.", http_status=404)
    async with request_engine(request).connect() as conn:
        w = await workspace(conn, name.lower())
    if w is None:
        raise ZidError("WORKSPACE_NOT_FOUND", "No such workspace.", http_status=404)
    return {
        "workspace": w["workspace"],
        "display_name": w["display_name"],
        "console_client_id": w["console_client_id"],
    }
