"""Policy administration API (ZID-AUTHZ-001 §18-19; UX-001 §10).

GET  /v1/policy-releases                       releases of every policy, newest first
GET  /v1/policy-releases/{id}                  source, cases, status, reviewers
POST /v1/policy-releases                       a DRAFT (policy.release.draft, R1)
PUT  /v1/policy-releases/{id}                  edit a DRAFT (its author)
POST /v1/policy-releases/{id}:validate         §18.1 checks + privilege diff; clean -> REVIEW
POST /v1/policy-releases/{id}:simulate         one request: candidate vs active (no change)
POST /v1/policy-releases/{id}:compare          many scenarios: a signed what-if ANALYSIS of the draft against the active release (CR-170)
POST /v1/policy-releases/{id}:review           an independent reviewer signs (never the author)
POST /v1/policy-releases/{id}:publish          REVIEW -> ACTIVE (policy.release.publish, R2;
                                               two independent reviewers for an R3 release)
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from zid_db import tenant_transaction
from zid_errors import ZidError

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.modules.commercial.ports import require_feature
from zid_platform.modules.policy_authoring.application import service
from zid_platform.modules.policy_authoring.infrastructure import release_store
from zid_platform.modules.policy_runtime.ports import PolicyError, PolicyRelease
from zid_platform.protected import catalog, protected_action

router = APIRouter(prefix="/v1/policy-releases", tags=["policy"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]
READ, DRAFT, PUBLISH = "policy.release.read", "policy.release.draft", "policy.release.publish"

_STATUS = {
    "POLICY_RELEASE_NOT_FOUND": 404,
    "POLICY_CASE_INVALID": 422,
    "POLICY_AUTHOR_ONLY": 403,
    "POLICY_REVIEWER_IS_AUTHOR": 403,
}


def http(e: PolicyError) -> ZidError:
    return ZidError(e.code, str(e), http_status=_STATUS.get(e.code, 409))


def _me(caller: Caller) -> str:
    if not caller.ctx.principal_id:
        raise ZidError(
            "UNAUTHENTICATED", "An authenticated principal is required.", http_status=401
        )
    return caller.ctx.principal_id


def view(r: PolicyRelease) -> dict[str, Any]:
    return {
        "release_id": r.release_id,
        "status": r.status.value,
        "policies": r.policies,
        "schema_version": r.schema_version,
        "content_hash": r.content_hash,
        "cases": [
            {
                "name": c.name,
                "principal": c.principal,
                "action": c.action,
                "resource": c.resource,
                "context": c.context,
                "expect_allow": c.expect_allow,
            }
            for c in r.cases
        ],
        "author_principal_id": r.author,
        "reviewers": list(r.reviewers),
        "released_at": r.effective_at.isoformat() if r.effective_at else None,
        "high_risk": service.high_risk(r, catalog()),
    }


class CaseIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    principal: str = Field(min_length=1, max_length=200)
    action: str = Field(min_length=1, max_length=120)
    resource: str = Field(min_length=1, max_length=200)
    context: dict[str, Any] = Field(default_factory=dict)
    expect_allow: bool = False


class DraftIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(pattern=r"^[a-z][a-z0-9-]{1,62}$")
    policies: str = Field(min_length=1, max_length=200_000)
    cases: list[CaseIn] = Field(min_length=1, max_length=500)


class EditIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    policies: str = Field(min_length=1, max_length=200_000)
    cases: list[CaseIn] = Field(min_length=1, max_length=500)


class SimulateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    principal: str = Field(min_length=1, max_length=200)
    action: str = Field(min_length=1, max_length=120)
    resource: str = Field(min_length=1, max_length=200)
    context: dict[str, Any] = Field(default_factory=dict)


def _cases(items: list[CaseIn]) -> Any:
    return service.parse_cases([c.model_dump() for c in items])


@router.get("")
async def list_releases(request: Request, caller: CallerDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(conn, caller, action=READ, resource="policy_release:*")
        return {"items": await release_store.list_releases(conn)}


@router.get("/{release_id}")
async def read_release(
    release_id: uuid.UUID, request: Request, caller: CallerDep
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(conn, caller, action=READ, resource=f"policy_release:{release_id}")
        try:
            return view(await release_store.load_release(conn, str(release_id)))
        except PolicyError as e:
            raise http(e) from e


@router.post("", status_code=201)
async def create(body: DraftIn, request: Request, caller: CallerDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(conn, caller, action=DRAFT, resource=f"policy_release:{body.name}")
        await require_feature(
            conn,
            "advanced.tenant-policy-automation-approval-routing",
            enforcement=getattr(request.app.state, "commercial_enforcement", "shadow"),
        )
        try:
            r = await service.create_draft(
                conn,
                caller.ctx.tenant_id or "",
                name=body.name,
                policies=body.policies,
                cases=_cases(body.cases),
                actor=_me(caller),
                now=datetime.now(UTC),
            )
        except PolicyError as e:
            raise http(e) from e
    return view(r)


@router.put("/{release_id}")
async def edit(
    release_id: uuid.UUID, body: EditIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(conn, caller, action=DRAFT, resource=f"policy_release:{release_id}")
        try:
            r = await service.edit_draft(
                conn,
                str(release_id),
                policies=body.policies,
                cases=_cases(body.cases),
                actor=_me(caller),
                now=datetime.now(UTC),
            )
        except PolicyError as e:
            raise http(e) from e
    return view(r)


@router.post("/{release_id}:validate")
async def validate(release_id: uuid.UUID, request: Request, caller: CallerDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(conn, caller, action=DRAFT, resource=f"policy_release:{release_id}")
        try:
            v = await service.validate(
                conn, str(release_id), catalog=catalog(), actor=_me(caller), now=datetime.now(UTC)
            )
        except PolicyError as e:
            raise http(e) from e
    return {
        **view(v.release),
        "problems": v.problems,
        "privilege_diff": {"newly_allowed": v.newly_allowed, "newly_denied": v.newly_denied},
    }


class WhatIfBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scenarios: list[SimulateIn] = Field(min_length=1, max_length=200)


@router.post("/{release_id}:compare", status_code=201)
async def compare(release_id: uuid.UUID, body: WhatIfBody, request: Request, caller: CallerDep) -> dict[str, Any]:
    """Run each scenario under the draft and under the active release, and keep the result as a signed ANALYSIS (never attached to a receipt)."""
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(conn, caller, action=READ, resource=f"policy_release:{release_id}")
        try:
            return await service.signed_what_if(
                conn, str(release_id), [s.model_dump() for s in body.scenarios], catalog=catalog(), requested_by=_me(caller), now=datetime.now(UTC)
            )
        except PolicyError as e:
            raise http(e) from e


@router.post("/{release_id}:simulate")
async def simulate(
    release_id: uuid.UUID, body: SimulateIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(conn, caller, action=READ, resource=f"policy_release:{release_id}")
        try:
            return await service.simulate(
                conn,
                str(release_id),
                principal=body.principal,
                action=body.action,
                resource=body.resource,
                context=body.context,
                catalog=catalog(),
            )
        except PolicyError as e:
            raise http(e) from e


@router.post("/{release_id}:review")
async def review(release_id: uuid.UUID, request: Request, caller: CallerDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn, caller, action=PUBLISH, resource=f"policy_release:{release_id}"
        )
        try:
            r = await service.review(
                conn, str(release_id), reviewer=_me(caller), now=datetime.now(UTC)
            )
        except PolicyError as e:
            raise http(e) from e
    return view(r)


@router.post("/{release_id}:publish")
async def publish(release_id: uuid.UUID, request: Request, caller: CallerDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn, caller, action=PUBLISH, resource=f"policy_release:{release_id}"
        )
        try:
            r = await service.publish(
                conn, str(release_id), catalog=catalog(), actor=_me(caller), now=datetime.now(UTC)
            )
        except PolicyError as e:
            raise http(e) from e
    return view(r)
