"""Tenant onboarding API (ZID-OVERLAY-001 §25-26; G-W1..W4).

    POST /v1/onboarding/organization     the pending bootstrap OWNER submits the organization
    GET  /v1/onboarding/gates            every gate of the tenant's current stage (machine-readable)
    GET  /v1/onboarding/plans                          plan versions            (OWNER/ADMIN)
    POST /v1/onboarding/plans                          new version              tenant.onboarding.manage
    POST /v1/onboarding/plans/{id}:approve             + second administrator   tenant.onboarding.manage
    POST /v1/onboarding/plans/{id}:validate            the current stage's gates
    POST /v1/onboarding/plans/{id}:start-integration   CONFIGURING -> INTEGRATING
    POST /v1/onboarding/plans/{id}:start-shadow        INTEGRATING -> SHADOW
    POST /v1/onboarding/plans/{id}:start-pilot         SHADOW -> PILOT     + approval  tenant.onboarding.activate
    POST /v1/onboarding/plans/{id}:activate            PILOT -> ACTIVE     + approval  tenant.onboarding.activate
    POST /v1/onboarding/plans/{id}:rollback            to the previous safe stage (no approval: safer)
    GET  /v1/onboarding/plans/{id}/stages              pilot scopes
    POST /v1/onboarding/plans/{id}/stages              plan a scope
    POST /v1/onboarding/stages/{id}:activate           a scope starts enforcing + approval

The tenant is always the caller's (INV-13). The registry adapter, its transport and its credential
are the cell's (never caller input); only the public registration identifier comes from the body.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from zid_db import tenant_transaction
from zid_errors import ErrorDetail, ZidError

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.country_gate import enforce_regulated, enforce_tenant, namespace_country
from zid_platform.modules.onboarding.application import gates as gate_service
from zid_platform.modules.onboarding.application import plans as plan_service
from zid_platform.modules.onboarding.application.organization import submit_organization
from zid_platform.modules.onboarding.domain.gates import OWNER_APPROVER, Gate, OnboardingError
from zid_platform.modules.onboarding.infrastructure import store as plan_store
from zid_platform.modules.onboarding.infrastructure.store import Plan, Stage
from zid_platform.modules.organization.ports import RegistryAdapter, adapter_for
from zid_platform.modules.tenant.ports import Mode, Role, RoleStatus, live_roles, matrix_gate
from zid_platform.modules.verification.ports import Capability
from zid_platform.protected import approved_by_second_admin, catalog, protected_action

router = APIRouter(prefix="/v1/onboarding", tags=["onboarding"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]
_STATUS = {
    "REGISTRY_UNAVAILABLE": 503,
    "TENANT_OWNER_UNAUTHORIZED": 403,
    "VALIDATION_FAILED": 422,
    "ORGANIZATION_SOURCE_UNSUPPORTED": 422,
    "APPROVER_IS_AUTHOR": 403,
}


def http(e: OnboardingError) -> ZidError:
    """Domain refusal -> HTTP; a gate refusal carries its failing gates as error details."""
    status = 404 if e.code.endswith("NOT_FOUND") else _STATUS.get(e.code, 409)
    blocked = [g for g in e.gates if g.blocking and g.status.value in ("FAIL", "PENDING")]
    return ZidError(
        e.code,
        e.detail or e.code.replace("_", " ").capitalize() + ".",
        http_status=status,
        retryable=e.code in ("REGISTRY_UNAVAILABLE", "CONCURRENT_MODIFICATION"),
        retry_after_ms=1000 if e.code == "REGISTRY_UNAVAILABLE" else None,
        details=[ErrorDetail(field=g.gate_id, issue=g.code or g.status.value) for g in blocked],
    )


def registry_adapter(request: Request, namespace: str) -> RegistryAdapter:
    broker = getattr(request.app.state, "secret_broker", None)

    def key() -> str | None:
        override = getattr(request.app.state, "registry_credential_override", None)
        if override:
            return str(override)  # the local/test register simulator (ZID_DEV_SIMULATORS)
        try:
            return str(broker.get("companies-house-api-key")) if broker else None
        except Exception:  # noqa: BLE001 - SECRET_UNAVAILABLE: the adapter reports REGISTRY_UNAVAILABLE
            return None

    try:
        return adapter_for(
            namespace,
            transport=getattr(request.app.state, "registry_transport", None),
            companies_house_key=key,
        )
    except Exception as e:  # RegistryError: unsupported namespace
        raise http(OnboardingError("ORGANIZATION_SOURCE_UNSUPPORTED", str(e))) from e


class OrganizationBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    namespace: str = Field(pattern=r"^(GB-COH)$")  # the national register the root comes from
    identifier: str = Field(min_length=6, max_length=20)
    lei: str | None = Field(default=None, min_length=20, max_length=20)  # optional corroboration


@router.get("/organization")
async def declared_organization(request: Request, caller: CallerDep) -> dict[str, Any]:
    """What the company declared at registration, to prefill company verification."""
    from zid_platform.modules.tenant.ports import declared_registration

    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        roles = await live_roles(conn, _me(caller))
        if not any(a.role in (Role.OWNER, Role.ADMIN) for a in roles):
            raise ZidError(
                "TENANT_ROLE_REQUIRED", "An OWNER or ADMIN role is required.", http_status=403
            )
        r = await declared_registration(conn, caller.ctx.tenant_id or "")
    if r is None:
        return {"declared": None}
    return {
        "declared": {
            "namespace": r.register_namespace,
            "identifier": r.register_identifier,
            "lei": r.lei,
            "notice_version": r.notice_version,
            "notice_accepted_at": r.notice_accepted_at.isoformat(),
            "home_cell_id": r.home_cell_id,
            "declared_at": r.declared_at.isoformat(),
        }
    }


@router.post("/organization")
async def organization(
    body: OrganizationBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    if not caller.ctx.principal_id:
        raise ZidError(
            "UNAUTHENTICATED", "An authenticated principal is required.", http_status=401
        )
    await enforce_regulated(  # EDR-001 and ZID-RCG-003
        request, namespace_country(body.namespace), Capability.BUSINESS_VERIFICATION, caller.ctx
    )
    adapter = registry_adapter(request, body.namespace)
    corroborate = [(registry_adapter(request, "LEI"), body.lei)] if body.lei else []
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        try:
            result = await submit_organization(
                conn,
                caller.ctx.tenant_id or "",
                principal_id=caller.ctx.principal_id,
                adapter=adapter,
                identifier=body.identifier,
                corroborate=corroborate,
                catalog=catalog(),
            )
        except OnboardingError as e:
            raise http(e) from e
    root = result.root
    return {
        "tenant_status": result.status,
        "gates": [g.view() for g in result.gates],
        "organization_id": root.organization_id if root else None,
        "authority_root_assertion_id": root.root_id if root else None,
        "legal_name": root.legal_name if root else None,
        "root_fresh_until": root.fresh_until.isoformat() if root else None,
        "admin_grant_id": result.admin_grant_id,
    }


@router.get("/gates")
async def gates(request: Request, caller: CallerDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        try:
            status, items = await gate_service.evaluate(conn, caller.ctx.tenant_id or "")
        except OnboardingError as e:
            raise http(e) from e
    return {"tenant_status": status, "gates": [g.view() for g in items]}


# ------------------------------------------------------------------------- plans (G-W4)

MANAGE, ACTIVATE = "tenant.onboarding.manage", "tenant.onboarding.activate"


def _me(caller: Caller) -> str:
    if not caller.ctx.principal_id:
        raise ZidError(
            "UNAUTHENTICATED", "An authenticated principal is required.", http_status=401
        )
    return caller.ctx.principal_id


def plan_view(p: Plan) -> dict[str, Any]:
    return {
        "plan_id": p.plan_id,
        "version": p.version,
        "supersedes": p.supersedes,
        "status": p.status.value,
        "target_mode": p.target_mode,
        "scope": p.scope,
        "exit_criteria": p.exit_criteria.view(),
        "cutover_windows": p.cutover_windows,
        "rollback_triggers": p.rollback_triggers,
        "support": p.support,
        "data_migration": p.data_migration,
        "author_principal_id": p.author,
        "approved_by": p.approved_by,
        "shadow_started_at": p.shadow_started_at.isoformat() if p.shadow_started_at else None,
        "pilot_started_at": p.pilot_started_at.isoformat() if p.pilot_started_at else None,
        "rollback_tested_at": p.rollback_tested_at.isoformat() if p.rollback_tested_at else None,
    }


def stage_view(st: Stage) -> dict[str, Any]:
    return {
        "stage_id": st.stage_id,
        "plan_id": st.plan_id,
        "name": st.name,
        "scope": st.scope.view(),
        "status": st.status.value,
        "approved_by": st.approved_by,
        "activated_at": st.activated_at.isoformat() if st.activated_at else None,
    }


def moved(status: str, gates: list[Gate]) -> dict[str, Any]:
    return {"tenant_status": status, "gates": [g.view() for g in gates]}


class PlanBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_mode: str = Field(pattern=r"^O[0-3]$")
    scope: dict[str, Any] = Field(default_factory=dict)
    exit_criteria: dict[str, Any] = Field(default_factory=dict)
    cutover_windows: list[dict[str, Any]] = Field(default_factory=list, max_length=20)
    rollback_triggers: dict[str, Any] = Field(default_factory=dict)
    support: dict[str, Any] = Field(default_factory=dict)
    data_migration: str = Field(default="NONE", max_length=500)
    acceptance_suite: list[str] = Field(default_factory=list, max_length=100)


class ApprovalBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Needed unless the company runs owner approval and the caller is its OWNER (CR-036)
    approval_request_id: str | None = Field(default=None, min_length=1, max_length=64)


class RollbackBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{1,63}$")


class StageBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    scope: dict[str, Any]


async def _owner_self_approves(request: Request, conn: Any, caller: Caller) -> bool:
    """Owner approval (CR-036): the cell runs it and the caller is this company's active OWNER."""
    if getattr(request.app.state, "onboarding_approval", "second_admin") != "owner":
        return False
    roles = await live_roles(conn, _me(caller))
    return any(a.role is Role.OWNER and a.status is RoleStatus.ACTIVE for a in roles)


async def _second_person(
    request: Request,
    conn: Any,
    caller: Caller,
    *,
    action: str,
    resource: str,
    change: str,
    approval_request_id: str | None,
) -> str:
    """The approver of a two-person step. Owner approval: the owner's own authority is checked as always and the step is
    recorded as approved by OWNER_APPROVER (the database refuses an approver equal to the author); otherwise an
    independent second administrator's approval is required."""
    if await _owner_self_approves(request, conn, caller):
        await protected_action(conn, caller, action=action, resource=resource, change=change)
        return OWNER_APPROVER
    if not approval_request_id:
        raise ZidError(
            "APPROVAL_REQUIRED",
            "A second administrator must approve this change first.",
            http_status=403,
        )
    return await approved_by_second_admin(
        conn,
        caller,
        action=action,
        resource=resource,
        change=change,
        approval_request_id=approval_request_id,
    )


@router.get("/mode")
async def approval_mode(request: Request, caller: CallerDep) -> dict[str, Any]:
    """How this company's two-person onboarding steps are approved, and whether the caller may approve them alone."""
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await _admin_read(conn, caller)
        return {
            "approval": getattr(request.app.state, "onboarding_approval", "second_admin"),
            "self_approve": await _owner_self_approves(request, conn, caller),
        }


@router.get("/connection")
async def connection_progress(request: Request, caller: CallerDep) -> dict[str, Any]:
    """What a person needs to connect an application, in one read: where to send decisions and how far shadow is.

    `shadow` is present while the company is in SHADOW or PILOT: the decisions seen so far against what the plan needs,
    each as a plain ratio, and `ready` when every shadow gate passes. Nothing here is a secret."""
    from zid_platform.modules.onboarding.domain.plan import shadow_gates
    from zid_platform.modules.tenant.ports import load_tenant

    tenant_id = caller.ctx.tenant_id or ""
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await _admin_read(conn, caller)
        tenant = await load_tenant(conn, tenant_id)
        plan = next(
            (p for p in await plan_store.plans(conn) if p.status.value != "SUPERSEDED"), None
        )
        shadow: dict[str, Any] | None = None
        if plan is not None and tenant is not None and tenant.status.value in ("SHADOW", "PILOT"):
            stats = await plan_store.shadow_stats(conn, plan.shadow_started_at or plan.created_at)
            c = plan.exit_criteria
            coverage = stats.context_complete / stats.observations if stats.observations else 0.0
            r3 = stats.r3_context_complete / stats.r3_observations if stats.r3_observations else 1.0
            agreement = 1 - stats.mismatches / stats.compared if stats.compared else None
            shadow = {
                "observations": stats.observations,
                "observations_needed": c.min_observations,
                "context_coverage": round(coverage, 4),
                "context_coverage_needed": c.min_context_coverage,
                "r3_context_coverage": round(r3, 4),
                "r3_context_coverage_needed": c.r3_context_coverage,
                "agreement": None if agreement is None else round(agreement, 4),
                "agreement_needed": round(1 - c.max_mismatch_rate, 4),
                "false_allows": stats.false_allows,
                "ready": all(g.status.value == "PASS" for g in shadow_gates(stats, c)),
            }
    token_settings = getattr(request.app.state, "token_settings", None)
    api_base = str(getattr(request.app.state, "token_audience", "") or "").rstrip("/")
    issuer = str(token_settings.issuer).rstrip("/") if token_settings is not None else api_base
    return {
        "api_base": api_base,
        "evaluation_url": f"{api_base}/access/v1/evaluation",
        "token_url": f"{issuer}/oauth2/token",
        "stage": tenant.status.value if tenant is not None else None,
        "shadow": shadow,
    }


async def _admin_read(conn: Any, caller: Caller) -> None:
    roles = await live_roles(conn, _me(caller))
    if not any(a.role in (Role.OWNER, Role.ADMIN) and a.status is RoleStatus.ACTIVE for a in roles):
        raise ZidError(
            "TENANT_ROLE_REQUIRED", "An OWNER or ADMIN role is required.", http_status=403
        )


@router.get(
    "/plans",
    dependencies=[Depends(matrix_gate("a.view-organization-dashboard", Mode.READ))],
)
async def list_plans(request: Request, caller: CallerDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await _admin_read(conn, caller)
        return {"items": [plan_view(p) for p in await plan_store.plans(conn)]}


@router.post(
    "/plans",
    status_code=201,
    dependencies=[Depends(matrix_gate("d.execute-ordinary-approved-operation", Mode.EXECUTE))],
)
async def create_plan(body: PlanBody, request: Request, caller: CallerDep) -> dict[str, Any]:
    tenant = caller.ctx.tenant_id or ""
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(conn, caller, action=MANAGE, resource="onboarding_plan:*")
        try:
            p = await plan_service.create_plan(conn, tenant, body.model_dump(), author=_me(caller))
        except OnboardingError as e:
            raise http(e) from e
    return plan_view(p)


@router.post("/plans/{plan_id}:approve")
async def approve_plan(
    plan_id: str, body: ApprovalBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    tenant = caller.ctx.tenant_id or ""
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        approver = await _second_person(
            request,
            conn,
            caller,
            action=MANAGE,
            resource=f"onboarding_plan:{plan_id}",
            change="APPROVE",
            approval_request_id=body.approval_request_id,
        )
        try:
            p = await plan_service.approve_plan(
                conn, tenant, plan_id, actor=_me(caller), approver=approver
            )
        except OnboardingError as e:
            raise http(e) from e
    return plan_view(p)


@router.post(
    "/plans/{plan_id}:validate",
    dependencies=[Depends(matrix_gate("d.execute-ordinary-approved-operation", Mode.EXECUTE))],
)
async def validate_plan(plan_id: str, request: Request, caller: CallerDep) -> dict[str, Any]:
    tenant = caller.ctx.tenant_id or ""
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await _admin_read(conn, caller)
        p = await plan_store.load_plan(conn, plan_id)
        if p is None:
            raise http(OnboardingError("ONBOARDING_PLAN_NOT_FOUND"))
        gates = await plan_service.stage_gates(conn, tenant, p)
        from zid_platform.modules.tenant.ports import load_tenant

        t = await load_tenant(conn, tenant)
    return moved(t.status.value if t else "UNKNOWN", gates)


@router.post(
    "/plans/{plan_id}:start-integration",
    dependencies=[Depends(matrix_gate("d.execute-ordinary-approved-operation", Mode.EXECUTE))],
)
async def start_integration(plan_id: str, request: Request, caller: CallerDep) -> dict[str, Any]:
    tenant = caller.ctx.tenant_id or ""
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(conn, caller, action=MANAGE, resource=f"onboarding_plan:{plan_id}")
        try:
            return moved(
                *await plan_service.start_integration(conn, tenant, plan_id, actor=_me(caller))
            )
        except OnboardingError as e:
            raise http(e) from e


@router.post(
    "/plans/{plan_id}:start-shadow",
    dependencies=[Depends(matrix_gate("d.execute-ordinary-approved-operation", Mode.EXECUTE))],
)
async def start_shadow(plan_id: str, request: Request, caller: CallerDep) -> dict[str, Any]:
    tenant = caller.ctx.tenant_id or ""
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(conn, caller, action=MANAGE, resource=f"onboarding_plan:{plan_id}")
        try:
            return moved(*await plan_service.start_shadow(conn, tenant, plan_id, actor=_me(caller)))
        except OnboardingError as e:
            raise http(e) from e


async def _approved_move(
    request: Request, caller: Caller, plan_id: str, body: ApprovalBody, which: str
) -> dict[str, Any]:
    tenant = caller.ctx.tenant_id or ""
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await enforce_tenant(request, conn, tenant, Capability.ENTERPRISE_USE)  # EDR-001
        approver = await _second_person(
            request,
            conn,
            caller,
            action=ACTIVATE,
            resource=f"onboarding_plan:{plan_id}",
            change=which,
            approval_request_id=body.approval_request_id,
        )
        fn = plan_service.start_pilot if which == "START_PILOT" else plan_service.activate
        try:
            return moved(*await fn(conn, tenant, plan_id, actor=_me(caller), approver=approver))
        except OnboardingError as e:
            raise http(e) from e


@router.post(
    "/plans/{plan_id}:start-pilot",
    dependencies=[Depends(matrix_gate("d.execute-ordinary-approved-operation", Mode.EXECUTE))],
)
async def start_pilot(
    plan_id: str, body: ApprovalBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    return await _approved_move(request, caller, plan_id, body, "START_PILOT")


@router.post(
    "/plans/{plan_id}:activate",
    dependencies=[Depends(matrix_gate("d.execute-ordinary-approved-operation", Mode.EXECUTE))],
)
async def activate(
    plan_id: str, body: ApprovalBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    return await _approved_move(request, caller, plan_id, body, "ACTIVATE")


@router.post(
    "/plans/{plan_id}:rollback",
    dependencies=[Depends(matrix_gate("d.execute-ordinary-approved-operation", Mode.EXECUTE))],
)
async def rollback(
    plan_id: str, body: RollbackBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    tenant = caller.ctx.tenant_id or ""
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(conn, caller, action=MANAGE, resource=f"onboarding_plan:{plan_id}")
        try:
            status = await plan_service.rollback(
                conn, tenant, plan_id, actor=_me(caller), reason=body.reason_code
            )
        except OnboardingError as e:
            raise http(e) from e
    return {"tenant_status": status}


@router.get(
    "/plans/{plan_id}/stages",
    dependencies=[Depends(matrix_gate("a.view-organization-dashboard", Mode.READ))],
)
async def list_stages(plan_id: str, request: Request, caller: CallerDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await _admin_read(conn, caller)
        return {
            "items": [
                stage_view(st) for st in await plan_store.stages(conn) if st.plan_id == plan_id
            ]
        }


@router.post(
    "/plans/{plan_id}/stages",
    status_code=201,
    dependencies=[Depends(matrix_gate("d.execute-ordinary-approved-operation", Mode.EXECUTE))],
)
async def add_stage(
    plan_id: str, body: StageBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    tenant = caller.ctx.tenant_id or ""
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(conn, caller, action=MANAGE, resource=f"onboarding_plan:{plan_id}")
        try:
            st = await plan_service.add_stage(
                conn, tenant, plan_id, name=body.name, scope=body.scope, actor=_me(caller)
            )
        except OnboardingError as e:
            raise http(e) from e
    return stage_view(st)


@router.post(
    "/stages/{stage_id}:activate",
    dependencies=[Depends(matrix_gate("d.execute-ordinary-approved-operation", Mode.EXECUTE))],
)
async def activate_stage(
    stage_id: str, body: ApprovalBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    tenant = caller.ctx.tenant_id or ""
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await enforce_tenant(request, conn, tenant, Capability.ENTERPRISE_USE)  # EDR-001
        approver = await _second_person(
            request,
            conn,
            caller,
            action=ACTIVATE,
            resource=f"onboarding_stage:{stage_id}",
            change="ACTIVATE",
            approval_request_id=body.approval_request_id,
        )
        try:
            st = await plan_service.activate_stage(
                conn, tenant, stage_id, actor=_me(caller), approver=approver
            )
        except OnboardingError as e:
            raise http(e) from e
    return stage_view(st)
