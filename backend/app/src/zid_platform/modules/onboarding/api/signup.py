"""Public company sign-up and workspace lookup (ZID-OVERLAY-001 §4; TENANCY-001 §14; G-W0).

    GET  /v1/signup/options               privacy-notice version, data locations, registers
    GET  /v1/signup/registry/{ns}/{id}    public register lookup: legal name and status only
    POST /v1/signup                       create a company tenant; you become its pending OWNER
    GET  /v1/workspaces/{workspace}       workspace name -> console client (to sign in there)

Unauthenticated by design: these are the only way in for someone who has no account yet. Sign-up
is off unless ZID_TENANT_SIGNUP_ENABLED is set; it is rate limited per client address, and it
returns a single-use passkey enrollment ticket for the new company's first principal (shown once).
The new OWNER becomes ACTIVE at the organization gate - in local/test with
ZID_TENANT_SIGNUP_DEV_VERIFY, a synthetic, self-labelled development verification instead.
Nothing about the person is stored: the passkey is the account. What the company declares - its
register identifier, the privacy-notice version acknowledged, the chosen data location - is stored
with the tenant (platform.tenant_registration) and re-checked against the register at verification.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field
from zid_db import in_tenant
from zid_errors import ZidError

from zid_platform.api_auth import request_engine
from zid_platform.country_gate import enforce, namespace_country
from zid_platform.modules.configuration.ports import public_branding
from zid_platform.modules.onboarding.application.organization import development_verification
from zid_platform.modules.onboarding.domain.gates import OnboardingError
from zid_platform.modules.organization.ports import RegistryError
from zid_platform.modules.tenant.ports import (
    ConsoleClient,
    Declaration,
    TenantError,
    self_signup,
    workspace,
)
from zid_platform.modules.verification.ports import Capability
from zid_platform.protected import catalog

router = APIRouter(tags=["signup"])

WINDOW_S, MAX_PER_WINDOW = 600.0, 5
LOOKUPS_PER_WINDOW = 30
_recent: dict[str, deque[float]] = defaultdict(deque)
_lookups: dict[str, deque[float]] = defaultdict(deque)


def _rate_limit(
    request: Request, bucket: dict[str, deque[float]] = _recent, limit: int = MAX_PER_WINDOW
) -> None:
    """In-process per-address bound (the edge rate limiter is the production control, DEC-G1)."""
    key = request.client.host if request.client else "unknown"
    now = time.monotonic()
    q = bucket[key]
    while q and now - q[0] > WINDOW_S:
        q.popleft()
    if len(q) >= limit:
        raise ZidError(
            "RATE_LIMITED",
            "Too many sign-ups from this address; try again later.",
            http_status=429,
            retryable=True,
            retry_after_ms=60_000,
        )
    q.append(now)


class RegistrationBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    namespace: str = Field(pattern=r"^(GB-COH)$")  # the registers the cell can verify against
    identifier: str = Field(min_length=6, max_length=20)
    lei: str | None = Field(default=None, min_length=20, max_length=20)


class SignupBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    company_name: str = Field(min_length=1, max_length=200)
    workspace: str = Field(
        min_length=1, max_length=63, pattern=r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$"
    )
    registration: RegistrationBody | None = None
    notice_version: str | None = Field(default=None, min_length=1, max_length=40)
    home_cell_id: str | None = Field(default=None, pattern=r"^[a-z0-9-]{1,40}$")


def _signup_open(request: Request) -> tuple[str, str]:
    """(this cell, current notice version), or SIGNUP_DISABLED."""
    if not getattr(request.app.state, "tenant_signup", False):
        raise ZidError("SIGNUP_DISABLED", "Company sign-up is not enabled here.", http_status=403)
    return (
        getattr(request.app.state, "home_cell", None) or "local-1",
        str(getattr(request.app.state, "tenant_signup_notice_version", "")),
    )


def _locations(request: Request, here: str) -> list[dict[str, Any]]:
    """Where a company's data can live: this cell plus the peer cells this deployment knows, as
    the cell catalogue describes them (recovery-only cells never take a new company)."""
    from zid_platform.modules.tenant.ports import cell_catalog

    cells = cell_catalog().cells
    endpoints: dict[str, str] = dict(getattr(request.app.state, "cell_endpoints", {}) or {})
    out = []
    for cell_id in [here, *sorted(c for c in endpoints if c != here)]:
        c = cells.get(cell_id)
        if c is not None and c.recovery_only:
            continue
        out.append(
            {
                "cell_id": cell_id,
                "region": c.region if c else None,
                "country": c.country if c else None,
                "legal_zone": c.legal_zone if c else None,
                "this_cell": cell_id == here,
                "signup_url": None if cell_id == here else endpoints.get(cell_id),
            }
        )
    return out


@router.get("/v1/signup/options")
async def options(request: Request) -> dict[str, Any]:
    here, notice = _signup_open(request)
    return {
        "notice_version": notice,
        "registers": [{"namespace": "GB-COH", "name": "Companies House", "country": "GB"}],
        "locations": _locations(request, here),
        "development_verification": bool(
            getattr(request.app.state, "tenant_signup_dev_verify", False)
        ),
    }


@router.get("/v1/signup/registry/{namespace}/{identifier}")
async def registry_lookup(namespace: str, identifier: str, request: Request) -> dict[str, Any]:
    """A public register lookup before registering: the legal name and status the register
    publishes, never its officers (personal data). Rate limited; nothing is stored."""
    _signup_open(request)
    _rate_limit(request, _lookups, LOOKUPS_PER_WINDOW)
    if namespace != "GB-COH":
        raise ZidError(
            "ORGANIZATION_SOURCE_UNSUPPORTED", "Only Companies House is supported.", http_status=422
        )
    enforce(request, namespace_country(namespace), Capability.BUSINESS_VERIFICATION)  # EDR-001
    from zid_platform.modules.onboarding.api.onboarding import registry_adapter

    adapter = registry_adapter(request, namespace)
    try:
        entity = await adapter.lookup(identifier, datetime.now(UTC))
    except RegistryError as e:
        status = {"REGISTRY_UNAVAILABLE": 503, "ORGANIZATION_NOT_FOUND": 404}.get(e.code, 422)
        raise ZidError(
            e.code,
            e.detail or "The register could not be checked.",
            http_status=status,
            retryable=status == 503,
        ) from e
    return {
        "namespace": entity.namespace,
        "identifier": entity.identifier,
        "legal_name": entity.legal_name,
        "active": entity.active,
        "status": entity.source_status,
        "jurisdiction": entity.jurisdiction,
    }


@router.post("/v1/signup", status_code=201)
async def signup(body: SignupBody, request: Request) -> dict[str, Any]:
    console = getattr(request.app.state, "tenant_console", None)
    here, notice = _signup_open(request)
    if console is None:
        raise ZidError("SIGNUP_DISABLED", "Company sign-up is not enabled here.", http_status=403)
    if body.home_cell_id and body.home_cell_id != here:
        # A company's data lives where it registers: send the registrant to that cell (TENANCY §14).
        url = dict(getattr(request.app.state, "cell_endpoints", {}) or {}).get(body.home_cell_id)
        raise ZidError(
            "CELL_ROUTING_REQUIRED",
            f"Register on the {body.home_cell_id} cell" + (f" at {url}" if url else "") + ".",
            http_status=421,
        )
    if body.registration is not None and body.notice_version != notice:
        raise ZidError(
            "NOTICE_VERSION_STALE",
            "Read and accept the current privacy notice, then try again.",
            http_status=422,
        )
    declaration = None
    if body.registration is not None:
        from zid_platform.modules.organization.ports import check_identifier

        try:
            ident = check_identifier(body.registration.namespace, body.registration.identifier)
            lei = check_identifier("LEI", body.registration.lei) if body.registration.lei else None
        except RegistryError as e:
            raise ZidError(
                e.code, e.detail or "The company number is not valid.", http_status=422
            ) from e
        declaration = Declaration(body.registration.namespace, ident, lei, notice)
        country = namespace_country(body.registration.namespace)  # EDR-001: registration is gated
        enforce(request, country, Capability.REGISTRATION)
        enforce(request, country, Capability.BUSINESS_VERIFICATION)
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
                home_cell_id=here,
                console=ConsoleClient(*console),
                registration=declaration,
            )
        except TenantError as e:
            code = 409 if e.code == "TENANT_SLUG_TAKEN" else 422
            raise ZidError(e.code, e.detail or "Sign-up failed.", http_status=code) from e
        status = result.tenant.status.value
        verified = False
        sim = getattr(request.app.state, "dev_simulator", None)
        async with in_tenant(conn, result.tenant.tenant_id):
            # How the registrant proves who they are (VERIFY-001 IAL-Z2 representative proofing).
            from zid_platform.modules.verification.ports import seed_representative_proofing

            await seed_representative_proofing(
                conn,
                result.tenant.tenant_id,
                now=datetime.now(UTC),
                jurisdictions=["GB"],
                simulator_jwks=sim.jwks() if sim is not None else None,
            )
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
        "home_cell_id": here,
        "registration_recorded": declaration is not None,
    }


@router.get("/v1/workspaces/{name}")
async def lookup(name: str, request: Request) -> dict[str, Any]:
    if not 1 <= len(name) <= 63:
        raise ZidError("WORKSPACE_NOT_FOUND", "No such workspace.", http_status=404)
    async with request_engine(request).connect() as conn:
        w = await workspace(conn, name.lower())
        branding: dict[str, str] = {}
        if (
            w is not None
        ):  # presentation-only settings the tenant chose, safe to show before sign-in
            async with in_tenant(conn, w["tenant_id"]):
                branding = await public_branding(conn)
    if w is None:
        raise ZidError("WORKSPACE_NOT_FOUND", "No such workspace.", http_status=404)
    return {
        "workspace": w["workspace"],
        "display_name": branding.get("display_name") or w["display_name"],
        "console_client_id": w["console_client_id"],
        "branding": {
            "accent": branding.get("accent", "blue"),
            "support_url": branding.get("support_url", ""),
        },
    }
