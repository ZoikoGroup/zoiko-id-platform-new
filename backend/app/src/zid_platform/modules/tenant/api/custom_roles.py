"""Custom organization roles (plan feature advanced.custom-organization-roles-permission-models).

GET  /v1/tenant/custom-roles                           the company's custom roles, with how many people hold each
GET  /v1/tenant/custom-roles/actions?base_role=&kind=   what a NARROW role may keep, or an EXTEND role may add, on OPERATOR or VIEWER
POST /v1/tenant/custom-roles                           define one (a plan feature)
POST /v1/tenant/custom-roles/{id}:retire               retire it; everyone who held it goes back to the plain base role
GET  /v1/tenant/custom-roles/{id}/members
POST /v1/tenant/custom-roles/{id}/members              give it to a person who holds the base role
POST /v1/tenant/custom-roles/{id}/members/{p}:revoke   take it away

A narrowing role only keeps part of its base role; an extension role adds only reads and requests (never approvals or administration). Either way the people
who can already invite an Operator or Viewer define and give them.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from zid_db import tenant_transaction
from zid_errors import ZidError

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.modules.commercial.ports import require_feature
from zid_platform.modules.tenant.domain.custom_roles import BASE_ROLES, PRIVILEGED, CustomRoleError, clean, extendable, holdable
from zid_platform.modules.tenant.domain.lifecycle import RoleStatus
from zid_platform.modules.tenant.domain.role_matrix import Mode
from zid_platform.modules.tenant.api.gate import matrix_gate
from zid_platform.modules.tenant.infrastructure import custom_role_store as store
from zid_platform.modules.tenant.infrastructure import tenant_store
from zid_platform.modules.tenant.infrastructure.role_matrix import role_matrix

router = APIRouter(prefix="/v1/tenant/custom-roles", tags=["tenant"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]
FEATURE = "advanced.custom-organization-roles-permission-models"
READ = matrix_gate("a.list-active-members-and-assigned-roles", Mode.READ)
CHANGE = matrix_gate("a.invite-operator-or-viewer", Mode.EXECUTE)


def _error(e: CustomRoleError) -> ZidError:
    status = 404 if e.code.endswith("NOT_FOUND") else 409 if e.code.endswith("EXISTS") else 422
    return ZidError(e.code, e.detail, http_status=status)


def _me(caller: Caller) -> str:
    if not caller.ctx.principal_id:
        raise ZidError("UNAUTHENTICATED", "An authenticated principal is required.", http_status=401)
    return caller.ctx.principal_id


class RoleBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=2, max_length=64)
    description: str = Field(default="", max_length=300)
    base_role: str = Field(min_length=1, max_length=16)
    kind: str = Field(default="NARROW", pattern="^(NARROW|EXTEND)$")
    actions: list[str] = Field(min_length=1, max_length=200)


class MemberBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    principal_id: uuid.UUID


@router.get("", dependencies=[Depends(READ)])
async def list_roles(request: Request, caller: CallerDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        return {"items": await store.listing(conn)}


@router.get("/actions", dependencies=[Depends(READ)])
async def role_actions(base_role: Annotated[str, Query(max_length=16)] = "OPERATOR", kind: Annotated[str, Query(pattern="^(NARROW|EXTEND)$")] = "NARROW") -> dict[str, Any]:
    if base_role not in BASE_ROLES:
        raise ZidError("CUSTOM_ROLE_INVALID", "The base role is OPERATOR or VIEWER.", http_status=422)
    return {"base_role": base_role, "kind": kind, "actions": (extendable if kind == "EXTEND" else holdable)(role_matrix(), base_role)}


@router.post("", status_code=201, dependencies=[Depends(CHANGE)])
async def define_role(body: RoleBody, request: Request, caller: CallerDep) -> dict[str, Any]:
    tenant = caller.ctx.tenant_id or ""
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await require_feature(conn, "advanced.custom-organization-roles-permission-models", enforcement=getattr(request.app.state, "commercial_enforcement", "shadow"))
        try:
            name, base, actions, description = clean(role_matrix(), name=body.name, base_role=body.base_role, actions=body.actions, description=body.description, kind=body.kind)
            return await store.create(conn, tenant, name=name, base_role=base, actions=actions, description=description, actor=_me(caller), kind=body.kind)
        except CustomRoleError as e:
            raise _error(e) from e


@router.post("/{role_id}:retire", dependencies=[Depends(CHANGE)])
async def retire_role(role_id: uuid.UUID, request: Request, caller: CallerDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        try:
            await store.retire(conn, str(role_id))
        except CustomRoleError as e:
            raise _error(e) from e
    return {"role_id": str(role_id), "status": "RETIRED"}


@router.get("/{role_id}/members", dependencies=[Depends(READ)])
async def role_members(role_id: uuid.UUID, request: Request, caller: CallerDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        try:
            await store.load(conn, str(role_id))
        except CustomRoleError as e:
            raise _error(e) from e
        return {"items": await store.members(conn, str(role_id))}


@router.post("/{role_id}/members", status_code=201, dependencies=[Depends(CHANGE)])
async def give_role(role_id: uuid.UUID, body: MemberBody, request: Request, caller: CallerDep) -> dict[str, Any]:
    tenant = caller.ctx.tenant_id or ""
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await require_feature(conn, "advanced.custom-organization-roles-permission-models", enforcement=getattr(request.app.state, "commercial_enforcement", "shadow"))
        try:
            role = await store.load(conn, str(role_id))
            if role.status != "ACTIVE":
                raise CustomRoleError("CUSTOM_ROLE_RETIRED", "That role is retired.")
            held = {a.role.value for a in await tenant_store.roles(conn, principal_id=str(body.principal_id)) if a.status is RoleStatus.ACTIVE}
            if held & PRIVILEGED:
                raise CustomRoleError("CUSTOM_ROLE_HOLDER_PRIVILEGED", "That person holds an administrative role; a custom role would not apply to them.")
            if role.base_role not in held:
                raise CustomRoleError("CUSTOM_ROLE_NEEDS_BASE", f"That person must hold the {role.base_role.title()} role first.")
            await store.assign(conn, tenant, str(role_id), str(body.principal_id), _me(caller))
        except CustomRoleError as e:
            raise _error(e) from e
    return {"role_id": str(role_id), "principal_id": str(body.principal_id)}


@router.post("/{role_id}/members/{principal_id}:revoke", dependencies=[Depends(CHANGE)])
async def take_role(role_id: uuid.UUID, principal_id: uuid.UUID, request: Request, caller: CallerDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        if not await store.unassign(conn, str(role_id), str(principal_id)):
            raise ZidError("CUSTOM_ROLE_NOT_HELD", "That person does not hold this role.", http_status=404)
    return {"role_id": str(role_id), "principal_id": str(principal_id), "revoked": True}
