"""Application registry and connector API (ZID-OVERLAY-001 §14-16, §26; G-W2).

GET  /v1/integration/applications                      list            (OWNER/ADMIN)
POST /v1/integration/applications                      register        integration.application.manage
POST /v1/integration/applications/{id}:transition      DRAFT/TESTING/ACTIVE/SUSPENDED/RETIRED
GET  /v1/integration/oauth-clients                     the company's OAuth clients (OWNER/ADMIN)
POST /v1/integration/oauth-clients                     register one; no secret exists  integration.application.manage
POST /v1/integration/oauth-clients/{id}:retire         stop an application signing in
GET  /v1/integration/connectors                        list + health   (OWNER/ADMIN)
GET  /v1/integration/connectors/{id}                   one, with credential fingerprints only
POST /v1/integration/connectors                        register        integration.connector.manage
PUT  /v1/integration/connectors/{id}/credential        write-only rotate  integration.connector.manage
POST /v1/integration/connectors/{id}:quarantine        immediate (the safer direction)
POST /v1/integration/connectors/{id}:release           + second administrator
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncConnection
from zid_db import tenant_transaction
from zid_errors import ZidError

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.modules.commercial.ports import require_feature, require_production_environment
from zid_platform.modules.integration.application import mapping, sandbox, service
from zid_platform.modules.integration.domain.model import (
    AppStatus,
    AuthProfile,
    Capability,
    ConnectorType,
    CredentialProfile,
    DisableBehavior,
    Environment,
    IntegrationError,
    Protocol,
)
from zid_platform.modules.integration.domain.routing import RouteKind
from zid_platform.modules.integration.infrastructure import mapping_store, store
from zid_platform.modules.integration.infrastructure.store import Application, Connector
from zid_platform.modules.tenant.ports import Mode, Role, RoleStatus, live_roles, matrix_gate
from zid_platform.protected import approved_by_second_admin, protected_action

router = APIRouter(prefix="/v1/integration", tags=["integration"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]
APP_ACTION, CONNECTOR_ACTION = "integration.application.manage", "integration.connector.manage"
_STATUS = {
    "VALIDATION_FAILED": 422,
    "PEP_OBLIGATION_UNSUPPORTED": 422,
    "MAPPING_TARGET_FORBIDDEN": 422,
    "REDIRECT_URI_INVALID": 422,
    "JWKS_INVALID": 422,
    "JWKS_PRIVATE_KEY": 422,
    "JWKS_NOT_ALLOWED": 422,
    "SCOPE_NOT_AVAILABLE": 422,
    "CLIENT_TYPE_INVALID": 422,
    "SANDBOX_REQUEST_INVALID": 422,
    "SANDBOX_LIMIT": 409,
}


def http(e: IntegrationError) -> ZidError:
    status = 404 if e.code.endswith("NOT_FOUND") else _STATUS.get(e.code, 409)
    return ZidError(
        e.code, e.detail or e.code.replace("_", " ").capitalize() + ".", http_status=status
    )


def _me(caller: Caller) -> str:
    if not caller.ctx.principal_id:
        raise ZidError(
            "UNAUTHENTICATED", "An authenticated principal is required.", http_status=401
        )
    return caller.ctx.principal_id


async def _admin_read(conn: AsyncConnection, caller: Caller) -> None:
    roles = await live_roles(conn, _me(caller))
    if not any(a.role in (Role.OWNER, Role.ADMIN) and a.status is RoleStatus.ACTIVE for a in roles):
        raise ZidError(
            "TENANT_ROLE_REQUIRED", "An OWNER or ADMIN role is required.", http_status=403
        )


def app_view(a: Application) -> dict[str, Any]:
    return {
        "application_id": a.application_id,
        "name": a.name,
        "environment": a.environment,
        "protocols": list(a.protocols),
        "redirect_uris": list(a.redirect_uris),
        "resource_ids": list(a.resource_ids),
        "credential_profile": a.credential_profile,
        "pep_profile": {
            "obligations": sorted(a.pep_obligations),
            "context_fields": sorted(a.pep_context_fields),
        },
        "risk_floor": f"R{a.risk_floor}",
        "evidence_floor": f"E{a.evidence_floor}",
        "owner_principal_id": a.owner_principal_id,
        "status": a.status.value,
        "configuration_revision": a.configuration_revision,
        "pep_client_id": a.pep_client_id,
    }


def connector_view(c: Connector) -> dict[str, Any]:
    return {
        "connector_id": c.connector_id,
        "connector_type": c.connector_type.value,
        "display_name": c.display_name,
        "source_ref": c.source_ref,
        "capabilities": sorted(c.capabilities),
        "source_of_truth_scope": sorted(c.source_of_truth_scope),
        "auth_profile": c.auth_profile,
        "health": c.health.value,
        "consecutive_failures": c.consecutive_failures,
        "last_success_at": c.last_success_at.isoformat() if c.last_success_at else None,
        "fresh_until": c.fresh_until.isoformat() if c.fresh_until else None,
        "owner_principal_id": c.owner_principal_id,
        "disable_behavior": c.disable_behavior,
        "quarantine_reason": c.quarantine_reason,
        "revision": c.revision,
    }


class AppBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    environment: Environment
    protocols: list[Protocol] = Field(min_length=1)
    redirect_uris: list[str] = Field(default_factory=list, max_length=20)
    resource_ids: list[str] = Field(default_factory=list, max_length=50)
    credential_profile: CredentialProfile = CredentialProfile.PRIVATE_KEY_JWT
    credential_justification: str | None = Field(default=None, max_length=1000)
    pep_obligations: list[str] = Field(default_factory=list, max_length=10)
    pep_context_fields: list[str] = Field(default_factory=list, max_length=30)
    risk_floor: int = Field(default=1, ge=0, le=3)
    evidence_floor: int = Field(default=1, ge=0, le=3)
    owner_principal_id: uuid.UUID | None = None
    pep_client_id: str | None = Field(default=None, min_length=1, max_length=256)


class Transition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: AppStatus


class ConnectorBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    connector_type: ConnectorType
    display_name: str = Field(min_length=1, max_length=200)
    source_ref: str | None = Field(default=None, max_length=120)
    capabilities: list[Capability] = Field(min_length=1)
    source_of_truth_scope: list[str] = Field(default_factory=list, max_length=30)
    auth_profile: AuthProfile = AuthProfile.OAUTH_CLIENT
    freshness_minutes: int | None = Field(default=None, ge=1, le=7 * 24 * 60)
    disable_behavior: DisableBehavior = DisableBehavior.FAIL_CLOSED_HIGH_RISK
    owner_principal_id: uuid.UUID | None = None


class CredentialBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    secret: str = Field(min_length=16, max_length=8192)
    not_after: datetime | None = None
    overlap_hours: int = Field(default=24, ge=0, le=168)


class QuarantineBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{1,63}$")


class ReleaseBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approval_request_id: str = Field(min_length=1, max_length=64)


@router.get(
    "/applications",
    dependencies=[Depends(matrix_gate("e.view-integration-directory-status", Mode.READ))],
)
async def list_applications(request: Request, caller: CallerDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await _admin_read(conn, caller)
        return {"items": [app_view(a) for a in await store.list_applications(conn)]}


@router.post(
    "/applications",
    status_code=201,
    dependencies=[Depends(matrix_gate("e.prepare-tenant-sso-external-trust-link", Mode.PREPARE))],
)
async def register_application(
    body: AppBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    tenant = caller.ctx.tenant_id or ""
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn, caller, action=APP_ACTION, resource="integration_application:*"
        )
        if (
            body.environment == Environment.PROD
        ):  # the plan's production environments (a retired application no longer counts)
            live = [
                a
                for a in await store.list_applications(conn)
                if a.environment == Environment.PROD.value and a.status != "RETIRED"
            ]
            await require_production_environment(
                conn,
                used=len(live),
                enforcement=getattr(request.app.state, "commercial_enforcement", "shadow"),
            )
        try:
            a = await service.register_application(
                conn,
                tenant,
                name=body.name,
                environment=body.environment,
                protocols=body.protocols,
                redirect_uris=body.redirect_uris,
                resource_ids=body.resource_ids,
                credential_profile=body.credential_profile,
                justification=body.credential_justification,
                pep_obligations=body.pep_obligations,
                pep_context_fields=body.pep_context_fields,
                risk_floor=body.risk_floor,
                evidence_floor=body.evidence_floor,
                owner=str(body.owner_principal_id or _me(caller)),
                actor=_me(caller),
                pep_client_id=body.pep_client_id,
            )
        except IntegrationError as e:
            raise http(e) from e
    return app_view(a)


@router.post(
    "/applications/{application_id}:transition",
    dependencies=[Depends(matrix_gate("e.prepare-tenant-sso-external-trust-link", Mode.PREPARE))],
)
async def transition_application(
    application_id: uuid.UUID, body: Transition, request: Request, caller: CallerDep
) -> dict[str, Any]:
    tenant = caller.ctx.tenant_id or ""
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn, caller, action=APP_ACTION, resource=f"integration_application:{application_id}"
        )
        try:
            a = await service.move_application(
                conn, tenant, str(application_id), body.status, actor=_me(caller)
            )
        except IntegrationError as e:
            raise http(e) from e
    return app_view(a)


class OAuthClientBody(BaseModel):
    """`purpose` SIGN_IN (default): a client people sign in through (redirect addresses required). DECISIONS: the machine client an application uses to
    ask the decision API (a signed key, no redirect address, no scopes to choose); a client people sign in through cannot get a machine token."""

    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=2, max_length=80)
    client_type: Literal["PUBLIC", "CONFIDENTIAL"]
    purpose: Literal["SIGN_IN", "DECISIONS"] = "SIGN_IN"
    redirect_uris: list[str] = Field(default_factory=list, max_length=10)
    scopes: list[str] = Field(default_factory=lambda: ["openid"], max_length=10)
    jwks: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _by_purpose(self) -> OAuthClientBody:
        if self.purpose == "DECISIONS":
            if self.jwks is None or self.redirect_uris or self.client_type != "CONFIDENTIAL":
                raise ValueError(
                    "a decisions client is CONFIDENTIAL, has public keys and no redirect addresses"
                )
        elif not self.redirect_uris:
            raise ValueError("a sign-in client needs at least one redirect address")
        return self


@router.get(
    "/oauth-clients",
    dependencies=[Depends(matrix_gate("e.view-integration-directory-status", Mode.READ))],
)
async def list_oauth_clients(request: Request, caller: CallerDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await _admin_read(conn, caller)
        return {"items": await service.client_gateway().listing(conn)}


@router.post(
    "/oauth-clients",
    status_code=201,
    dependencies=[Depends(matrix_gate("e.prepare-tenant-sso-external-trust-link", Mode.PREPARE))],
)
async def register_oauth_client(
    body: OAuthClientBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    tenant = caller.ctx.tenant_id or ""
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn, caller, action=APP_ACTION, resource="integration_application:*"
        )
        try:
            if body.purpose == "DECISIONS":
                assert body.jwks is not None  # the model checks it
                await require_feature(
                    conn,
                    "standard.service-identities-m2m-management",
                    enforcement=getattr(request.app.state, "commercial_enforcement", "shadow"),
                )
                c = await service.register_decision_client(
                    conn,
                    tenant,
                    name=body.name,
                    jwks=body.jwks,
                    audience=request.app.state.token_audience,
                    home_cell=str(getattr(request.app.state, "home_cell", "local-1")),
                    actor=_me(caller),
                )
            else:
                c = await service.register_oauth_client(
                    conn,
                    tenant,
                    name=body.name,
                    client_type=body.client_type,
                    redirect_uris=body.redirect_uris,
                    jwks=body.jwks,
                    scopes=body.scopes,
                    audience=request.app.state.token_audience,
                    actor=_me(caller),
                )
        except IntegrationError as e:
            raise http(e) from e
    return c


@router.post(
    "/oauth-clients/{client_id}:retire",
    dependencies=[Depends(matrix_gate("e.revoke-compromised-tenant-integration", Mode.EXECUTE))],
)
async def retire_oauth_client(
    client_id: str, request: Request, caller: CallerDep
) -> dict[str, Any]:
    tenant = caller.ctx.tenant_id or ""
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn, caller, action=APP_ACTION, resource=f"integration_application:{client_id}"
        )
        try:
            await service.retire_oauth_client(conn, tenant, client_id, actor=_me(caller))
        except IntegrationError as e:
            raise http(e) from e
    return {"client_id": client_id, "status": "RETIRED"}


@router.get(
    "/connectors",
    dependencies=[Depends(matrix_gate("e.view-integration-directory-status", Mode.READ))],
)
async def list_connectors(request: Request, caller: CallerDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await _admin_read(conn, caller)
        return {"items": [connector_view(c) for c in await store.list_connectors(conn)]}


@router.get(
    "/connectors/{connector_id}",
    dependencies=[Depends(matrix_gate("e.view-integration-directory-status", Mode.READ))],
)
async def read_connector(
    connector_id: uuid.UUID, request: Request, caller: CallerDep
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await _admin_read(conn, caller)
        c = await store.load_connector(conn, str(connector_id))
        if c is None:
            raise http(IntegrationError("CONNECTOR_NOT_FOUND"))
        creds = await store.credentials(conn, str(connector_id))
    return {
        **connector_view(c),
        "credentials": [  # never the value: fingerprints and lifecycle only (§16)
            {
                "version": m.version,
                "status": m.status,
                "fingerprint": m.fingerprint,
                "not_after": m.not_after.isoformat() if m.not_after else None,
                "overlap_until": m.overlap_until.isoformat() if m.overlap_until else None,
            }
            for m in creds
        ],
    }


@router.post(
    "/connectors",
    status_code=201,
    dependencies=[Depends(matrix_gate("e.prepare-tenant-sso-external-trust-link", Mode.PREPARE))],
)
async def register_connector(
    body: ConnectorBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    tenant = caller.ctx.tenant_id or ""
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn, caller, action=CONNECTOR_ACTION, resource="integration_connector:*"
        )
        try:
            c = await service.register_connector(
                conn,
                tenant,
                connector_type=body.connector_type,
                display_name=body.display_name,
                source_ref=body.source_ref,
                capabilities=body.capabilities,
                source_of_truth_scope=body.source_of_truth_scope,
                auth_profile=body.auth_profile,
                freshness=timedelta(minutes=body.freshness_minutes)
                if body.freshness_minutes
                else None,
                owner=str(body.owner_principal_id or _me(caller)),
                disable_behavior=body.disable_behavior,
                actor=_me(caller),
            )
        except IntegrationError as e:
            raise http(e) from e
    return connector_view(c)


@router.put(
    "/connectors/{connector_id}/credential",
    dependencies=[
        Depends(matrix_gate("e.rotate-assigned-nonprivileged-api-credential", Mode.EXECUTE))
    ],
)
async def put_credential(
    connector_id: uuid.UUID, body: CredentialBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    tenant = caller.ctx.tenant_id or ""
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn, caller, action=CONNECTOR_ACTION, resource=f"integration_connector:{connector_id}"
        )
        try:
            version = await service.set_credential(
                conn,
                tenant,
                str(connector_id),
                secret=body.secret,
                not_after=body.not_after,
                overlap=timedelta(hours=body.overlap_hours),
                actor=_me(caller),
            )
        except IntegrationError as e:
            raise http(e) from e
    return {"connector_id": str(connector_id), "version": version}  # the value is never echoed


@router.post(
    "/connectors/{connector_id}:quarantine",
    dependencies=[Depends(matrix_gate("e.revoke-compromised-tenant-integration", Mode.EXECUTE))],
)
async def quarantine(
    connector_id: uuid.UUID, body: QuarantineBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    tenant = caller.ctx.tenant_id or ""
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn, caller, action=CONNECTOR_ACTION, resource=f"integration_connector:{connector_id}"
        )
        try:
            c = await service.quarantine(
                conn, tenant, str(connector_id), reason=body.reason_code, actor=_me(caller)
            )
        except IntegrationError as e:
            raise http(e) from e
    return connector_view(c)


@router.post(
    "/connectors/{connector_id}:release",
    dependencies=[Depends(matrix_gate("e.revoke-compromised-tenant-integration", Mode.EXECUTE))],
)
async def release(
    connector_id: uuid.UUID, body: ReleaseBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    tenant = caller.ctx.tenant_id or ""
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        approver = await approved_by_second_admin(
            conn,
            caller,
            action=CONNECTOR_ACTION,
            resource=f"integration_connector:{connector_id}",
            change="RELEASE",
            approval_request_id=body.approval_request_id,
        )
        try:
            c = await service.release(
                conn, tenant, str(connector_id), actor=_me(caller), approver=approver
            )
        except IntegrationError as e:
            raise http(e) from e
    return connector_view(c)


# ------------------------------------------------------------------- mapping releases (G-W3)

MAPPING_ACTION = "integration.mapping.manage"


def release_view(r: mapping_store.Release) -> dict[str, Any]:
    return {
        "release_id": r.release_id,
        "connector_id": r.connector_id,
        "version": r.version,
        "status": r.status.value,
        "rules": r.rules,
        "content_hash": r.content_hash,
        "author_principal_id": r.author,
        "approved_by": r.approved_by,
        "simulation_report": r.simulation_report,
        "activated_at": r.activated_at.isoformat() if r.activated_at else None,
    }


def route_view(r: Any) -> dict[str, Any]:
    return {
        "rule_id": r.rule_id,
        "kind": r.kind.value,
        "match": r.match,
        "connection_ref": r.connection_ref,
    }


class MappingBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rules: list[dict[str, Any]] = Field(min_length=1, max_length=200)


class SimulateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    records: list[dict[str, Any]] = Field(min_length=1, max_length=1000)


class RouteBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: RouteKind
    match: str = Field(min_length=1, max_length=253)
    connection_ref: str = Field(min_length=10, max_length=120)


class ResolveBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    application_id: str | None = Field(default=None, max_length=64)
    login_domain: str | None = Field(default=None, max_length=253)


@router.get(
    "/connectors/{connector_id}/mapping-releases",
    dependencies=[Depends(matrix_gate("e.view-integration-directory-status", Mode.READ))],
)
async def list_releases(
    connector_id: uuid.UUID, request: Request, caller: CallerDep
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await _admin_read(conn, caller)
        items = await mapping_store.releases_for(conn, str(connector_id))
    return {"items": [release_view(r) for r in items]}


@router.post(
    "/connectors/{connector_id}/mapping-releases",
    status_code=201,
    dependencies=[Depends(matrix_gate("e.prepare-tenant-sso-external-trust-link", Mode.PREPARE))],
)
async def create_release(
    connector_id: uuid.UUID, body: MappingBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    tenant = caller.ctx.tenant_id or ""
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(conn, caller, action=MAPPING_ACTION, resource="mapping_release:*")
        try:
            r = await mapping.create_release(
                conn, tenant, str(connector_id), rules=body.rules, author=_me(caller)
            )
        except IntegrationError as e:
            raise http(e) from e
    return release_view(r)


@router.post(
    "/mapping-releases/{release_id}:simulate",
    dependencies=[Depends(matrix_gate("e.prepare-tenant-sso-external-trust-link", Mode.PREPARE))],
)
async def simulate(
    release_id: uuid.UUID, body: SimulateBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    tenant = caller.ctx.tenant_id or ""
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn, caller, action=MAPPING_ACTION, resource=f"mapping_release:{release_id}"
        )
        try:
            r = await mapping.simulate_release(
                conn, tenant, str(release_id), records=body.records, actor=_me(caller)
            )
        except IntegrationError as e:
            raise http(e) from e
    return release_view(r)


@router.post(
    "/mapping-releases/{release_id}:activate",
    dependencies=[
        Depends(matrix_gate("e.activate-sensitive-production-integration", Mode.PREPARE))
    ],
)
async def activate(
    release_id: uuid.UUID, body: ReleaseBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    """Activation and rollback alike: an approval bound to this exact release content."""
    tenant = caller.ctx.tenant_id or ""
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        current = await mapping_store.load_release(conn, str(release_id))
        if current is None:
            raise http(IntegrationError("MAPPING_RELEASE_NOT_FOUND"))
        approver = await approved_by_second_admin(
            conn,
            caller,
            action=MAPPING_ACTION,
            resource=f"mapping_release:{release_id}",
            change=f"ACTIVATE:{current.content_hash}",
            approval_request_id=body.approval_request_id,
        )
        try:
            r = await mapping.activate_release(
                conn, tenant, str(release_id), actor=_me(caller), approver=approver
            )
        except IntegrationError as e:
            raise http(e) from e
    return release_view(r)


@router.get(
    "/routes",
    dependencies=[Depends(matrix_gate("e.view-integration-directory-status", Mode.READ))],
)
async def list_routes(request: Request, caller: CallerDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await _admin_read(conn, caller)
        items = await mapping_store.routes(conn)
    return {"items": [route_view(r) for r in items]}


@router.post(
    "/routes",
    status_code=201,
    dependencies=[Depends(matrix_gate("e.prepare-tenant-sso-external-trust-link", Mode.PREPARE))],
)
async def add_route(body: RouteBody, request: Request, caller: CallerDep) -> dict[str, Any]:
    tenant = caller.ctx.tenant_id or ""
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn, caller, action="tenant.identity_provider.change", resource=body.connection_ref
        )
        try:
            r = await mapping.add_route(
                conn,
                tenant,
                kind=body.kind,
                match=body.match,
                connection_ref=body.connection_ref,
                actor=_me(caller),
            )
        except IntegrationError as e:
            raise http(e) from e
    return route_view(r)


@router.post("/routes:resolve")
async def resolve_route(body: ResolveBody, request: Request, caller: CallerDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await _admin_read(conn, caller)
        r = await mapping.route_login(
            conn, application_id=body.application_id, login_domain=body.login_domain
        )
    return {"connection_ref": r.connection_ref if r else None, "rule_id": r.rule_id if r else None}


# ---------------------------------------------------------------- developer sandbox test identities


class SandboxIdentitiesBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prefix: str = Field(default="test", pattern=r"^[a-z][a-z0-9-]{0,30}$")
    count: int = Field(default=1, ge=1, le=sandbox.MAX_PER_REQUEST)
    days: int = Field(default=14, ge=1, le=sandbox.MAX_DAYS)


@router.get(
    "/sandbox-identities",
    dependencies=[Depends(matrix_gate("e.view-integration-directory-status", Mode.READ))],
)
async def list_sandbox_identities(request: Request, caller: CallerDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await _admin_read(conn, caller)
        return {"items": await sandbox.listing(conn), "max_live": sandbox.MAX_LIVE}


@router.post(
    "/sandbox-identities",
    status_code=201,
    dependencies=[Depends(matrix_gate("e.prepare-tenant-sso-external-trust-link", Mode.PREPARE))],
)
async def create_sandbox_identities(
    body: SandboxIdentitiesBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    tenant = caller.ctx.tenant_id or ""
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await _admin_read(conn, caller)
        try:
            made = await sandbox.create(
                conn,
                tenant,
                prefix=body.prefix,
                count=body.count,
                days=body.days,
                actor=_me(caller),
                home_cell=str(getattr(request.app.state, "home_cell", "local-1")),
            )
        except IntegrationError as e:
            raise http(e) from e
    return {"items": made}


@router.post(
    "/sandbox-identities/{principal_id}:retire",
    dependencies=[Depends(matrix_gate("e.revoke-compromised-tenant-integration", Mode.EXECUTE))],
)
async def retire_sandbox_identity(
    principal_id: uuid.UUID, request: Request, caller: CallerDep
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await _admin_read(conn, caller)
        try:
            await sandbox.retire(conn, str(principal_id))
        except IntegrationError as e:
            raise http(e) from e
    return {"principal_id": str(principal_id), "status": "RETIRED"}
