"""Organization hierarchy (plan feature advanced.multi-org-hierarchy-group-governance).

GET  /v1/tenant/org-units                          the whole tree, flat with depth and parent, and how many people each holds
POST /v1/tenant/org-units                          add a unit under a parent (or a top-level unit)
POST /v1/tenant/org-units/{id}:retire              retire a unit that has no live sub-units
POST /v1/tenant/org-units/{id}/members             place a person in a unit
POST /v1/tenant/org-units/{id}/members/{p}:remove

A hierarchy is a record of structure inside one tenant. Defining one is a change of the organization's profile, so it is run by the people who can
edit it; reading it is open to anyone who may list members.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from zid_db import tenant_transaction
from zid_errors import ZidError

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.modules.commercial.ports import require_feature
from zid_platform.modules.tenant.api.gate import matrix_gate
from zid_platform.modules.tenant.domain.role_matrix import Mode

router = APIRouter(prefix="/v1/tenant/org-units", tags=["tenant"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]
READ = matrix_gate("a.list-active-members-and-assigned-roles", Mode.READ)
EDIT = matrix_gate("a.edit-ordinary-organization-profile", Mode.EXECUTE)
MAX_DEPTH = 5  # six levels: 0..5
MAX_UNITS = 500


class UnitBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=2, max_length=100)
    kind: Literal["GROUP", "SUBSIDIARY", "DIVISION", "DEPARTMENT"]
    parent_unit_id: uuid.UUID | None = None


class MemberBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    principal_id: uuid.UUID
    title: str = Field(default="", max_length=80)


def _me(caller: Caller) -> str:
    if not caller.ctx.principal_id:
        raise ZidError(
            "UNAUTHENTICATED", "An authenticated principal is required.", http_status=401
        )
    return caller.ctx.principal_id


@router.get("", dependencies=[Depends(READ)])
async def tree(request: Request, caller: CallerDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT u.*, (SELECT count(*) FROM platform.org_unit_member m WHERE m.unit_id = u.unit_id AND m.removed_at IS NULL) AS members "
                    "FROM platform.org_unit u WHERE u.status = 'ACTIVE' ORDER BY u.depth, lower(u.name)"
                )
            )
        ).all()
    return {
        "max_depth": MAX_DEPTH + 1,
        "items": [
            {
                "unit_id": str(r.unit_id),
                "parent_unit_id": str(r.parent_unit_id) if r.parent_unit_id else None,
                "name": r.name,
                "kind": r.kind,
                "depth": r.depth,
                "members": int(r.members),
            }
            for r in rows
        ],
    }


@router.post("", status_code=201, dependencies=[Depends(EDIT)])
async def add_unit(body: UnitBody, request: Request, caller: CallerDep) -> dict[str, Any]:
    tenant = caller.ctx.tenant_id or ""
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await require_feature(
            conn,
            "advanced.multi-org-hierarchy-group-governance",
            enforcement=getattr(request.app.state, "commercial_enforcement", "shadow"),
        )
        depth = 0
        if body.parent_unit_id:
            parent = (
                await conn.execute(
                    text("SELECT depth, status FROM platform.org_unit WHERE unit_id = :p"),
                    {"p": body.parent_unit_id},
                )
            ).first()
            if parent is None or parent.status != "ACTIVE":
                raise ZidError("ORG_UNIT_NOT_FOUND", "No such live parent unit.", http_status=404)
            depth = parent.depth + 1
            if depth > MAX_DEPTH:
                raise ZidError(
                    "ORG_UNIT_TOO_DEEP",
                    f"A hierarchy has at most {MAX_DEPTH + 1} levels.",
                    http_status=422,
                )
        if (
            await conn.execute(
                text("SELECT count(*) FROM platform.org_unit WHERE status = 'ACTIVE'")
            )
        ).scalar_one() >= MAX_UNITS:
            raise ZidError("ORG_UNIT_LIMIT", f"At most {MAX_UNITS} units.", http_status=409)
        uid = uuid.uuid4()
        try:
            async with conn.begin_nested():
                await conn.execute(
                    text(
                        "INSERT INTO platform.org_unit (unit_id, tenant_id, parent_unit_id, name, kind, depth, created_by) "
                        "VALUES (:id, :t, :p, :n, :k, :d, :u)"
                    ),
                    {
                        "id": uid,
                        "t": uuid.UUID(tenant),
                        "p": body.parent_unit_id,
                        "n": body.name.strip(),
                        "k": body.kind,
                        "d": depth,
                        "u": uuid.UUID(_me(caller)),
                    },
                )
        except IntegrityError as e:
            raise ZidError(
                "ORG_UNIT_EXISTS", "A unit with that name already exists there.", http_status=409
            ) from e
    return {"unit_id": str(uid), "depth": depth}


@router.post("/{unit_id}:retire", dependencies=[Depends(EDIT)])
async def retire_unit(unit_id: uuid.UUID, request: Request, caller: CallerDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        if (
            await conn.execute(
                text(
                    "SELECT count(*) FROM platform.org_unit WHERE parent_unit_id = :u AND status = 'ACTIVE'"
                ),
                {"u": unit_id},
            )
        ).scalar_one():
            raise ZidError("ORG_UNIT_HAS_CHILDREN", "Retire its sub-units first.", http_status=409)
        res = await conn.execute(
            text(
                "UPDATE platform.org_unit SET status = 'RETIRED', retired_at = now() WHERE unit_id = :u AND status = 'ACTIVE'"
            ),
            {"u": unit_id},
        )
        if res.rowcount != 1:
            raise ZidError("ORG_UNIT_NOT_FOUND", "No such live unit.", http_status=404)
        await conn.execute(
            text(
                "UPDATE platform.org_unit_member SET removed_at = now() WHERE unit_id = :u AND removed_at IS NULL"
            ),
            {"u": unit_id},
        )
    return {"unit_id": str(unit_id), "status": "RETIRED"}


@router.post("/{unit_id}/members", status_code=201, dependencies=[Depends(EDIT)])
async def add_member(
    unit_id: uuid.UUID, body: MemberBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    tenant = caller.ctx.tenant_id or ""
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await require_feature(
            conn,
            "advanced.multi-org-hierarchy-group-governance",
            enforcement=getattr(request.app.state, "commercial_enforcement", "shadow"),
        )
        live = (
            await conn.execute(
                text("SELECT 1 FROM platform.org_unit WHERE unit_id = :u AND status = 'ACTIVE'"),
                {"u": unit_id},
            )
        ).first()
        if live is None:
            raise ZidError("ORG_UNIT_NOT_FOUND", "No such live unit.", http_status=404)
        person = (
            await conn.execute(
                text("SELECT 1 FROM core.principal WHERE principal_id = :p AND status = 'ACTIVE'"),
                {"p": body.principal_id},
            )
        ).first()
        if person is None:
            raise ZidError(
                "PRINCIPAL_NOT_FOUND", "No such active person in this company.", http_status=404
            )
        await conn.execute(
            text(
                "INSERT INTO platform.org_unit_member (unit_id, principal_id, tenant_id, title, added_by) VALUES (:u, :p, :t, :ti, :a) "
                "ON CONFLICT (unit_id, principal_id) DO UPDATE SET removed_at = NULL, title = :ti, added_by = :a, added_at = now()"
            ),
            {
                "u": unit_id,
                "p": body.principal_id,
                "t": uuid.UUID(tenant),
                "ti": body.title.strip(),
                "a": uuid.UUID(_me(caller)),
            },
        )
    return {"unit_id": str(unit_id), "principal_id": str(body.principal_id)}


@router.post("/{unit_id}/members/{principal_id}:remove", dependencies=[Depends(EDIT)])
async def remove_member(
    unit_id: uuid.UUID, principal_id: uuid.UUID, request: Request, caller: CallerDep
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        res = await conn.execute(
            text(
                "UPDATE platform.org_unit_member SET removed_at = now() WHERE unit_id = :u AND principal_id = :p AND removed_at IS NULL"
            ),
            {"u": unit_id, "p": principal_id},
        )
    if res.rowcount != 1:
        raise ZidError(
            "ORG_UNIT_MEMBER_NOT_FOUND", "That person is not in this unit.", http_status=404
        )
    return {"unit_id": str(unit_id), "principal_id": str(principal_id), "removed": True}
