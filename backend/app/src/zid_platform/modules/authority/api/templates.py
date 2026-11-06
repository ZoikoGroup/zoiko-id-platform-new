"""Delegation workflow templates (plan feature advanced.advanced-delegation-workflow-templates).

    GET  /v1/delegation-templates                 the company's templates
    POST /v1/delegation-templates                 define one
    POST /v1/delegation-templates/{id}:retire
    POST /v1/delegation-templates/{id}:apply      issue a grant from it to a person (the caller is the grantor)

Applying a template is an ordinary grant issue: the same attenuation checks run, so a template never lets someone hand out more than they hold.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from zid_context import uuid7
from zid_db import tenant_transaction
from zid_errors import ZidError

from zid_platform.api_auth import Caller, authenticated_caller, http_error, request_engine
from zid_platform.modules.authority.application.service import issue_grant
from zid_platform.modules.authority.domain.algebra import ANY, Authority, ValueLimit, Window
from zid_platform.modules.authority.domain.grants import AuthorityError, ContinuityMode, Grant, GrantStatus, GrantType
from zid_platform.modules.commercial.ports import require_feature

router = APIRouter(prefix="/v1/delegation-templates", tags=["authority"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]


class TemplateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=2, max_length=80)
    description: str = Field(default="", max_length=300)
    actions: list[str] = Field(min_length=1, max_length=50)
    resources: list[str] = Field(min_length=1, max_length=50)
    purposes: list[str] | None = Field(default=None, max_length=20)
    value_amount: str | None = None
    value_currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    max_days: int = Field(default=30, ge=1, le=365)
    delegation_allowed: bool = False
    max_depth: int = Field(default=0, ge=0, le=16)
    min_assurance: int = Field(default=0, ge=0, le=3)
    risk_class: int = Field(default=3, ge=0, le=3)


class ApplyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    grantee_principal_id: str
    represented_principal_id: str
    authority_root_assertion_id: str | None = None
    parent_grant_id: str | None = None
    days: int | None = Field(default=None, ge=1, le=365)


def _view(r: Any) -> dict[str, Any]:
    return {
        "template_id": str(r.template_id),
        "name": r.name,
        "description": r.description,
        "actions": list(r.actions),
        "resources": list(r.resources),
        "purposes": list(r.purposes) if r.purposes is not None else None,
        "value_limit": {"amount": str(r.value_amount), "currency": r.value_currency} if r.value_amount is not None else None,
        "max_days": r.max_days,
        "delegation": {"allowed": r.delegation_allowed, "max_depth": r.max_depth},
        "min_assurance": r.min_assurance,
        "risk_class": r.risk_class,
        "status": r.status,
        "created_at": r.created_at.isoformat(),
    }


def _me(caller: Caller) -> str:
    if not caller.ctx.principal_id:
        raise ZidError("UNAUTHENTICATED", "An authenticated principal is required.", http_status=401)
    return caller.ctx.principal_id


@router.get("")
async def list_templates(request: Request, caller: CallerDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        rows = (await conn.execute(text("SELECT * FROM authz.delegation_template ORDER BY status, lower(name)"))).all()
    return {"items": [_view(r) for r in rows]}


@router.post("", status_code=201)
async def define_template(body: TemplateBody, request: Request, caller: CallerDep) -> dict[str, Any]:
    if (body.value_amount is None) != (body.value_currency is None):
        raise http_error("VALIDATION_FAILED", "Give both a value limit amount and its currency, or neither.")
    try:
        amount = Decimal(body.value_amount) if body.value_amount is not None else None
        if amount is not None and (amount < 0 or not amount.is_finite()):
            raise ValueError
    except (InvalidOperation, ValueError) as exc:
        raise http_error("VALIDATION_FAILED", "The value limit must be a non-negative decimal.") from exc
    if not body.delegation_allowed and body.max_depth:
        raise http_error("VALIDATION_FAILED", "A depth needs delegation to be allowed.")
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await require_feature(
            conn,
            "advanced.advanced-delegation-workflow-templates",
            enforcement=getattr(request.app.state, "commercial_enforcement", "shadow"),
        )
        try:
            async with conn.begin_nested():
                row = (
                    await conn.execute(
                        text(
                            "INSERT INTO authz.delegation_template (template_id, tenant_id, name, description, actions, resources, purposes, value_amount, "
                            "value_currency, max_days, delegation_allowed, max_depth, min_assurance, risk_class, created_by) "
                            "VALUES (:id, :t, :n, :d, :a, :r, :p, :va, :vc, :md, :da, :dp, :ma, :rc, :u) RETURNING *"
                        ),
                        {
                            "id": uuid.uuid4(), "t": uuid.UUID(caller.ctx.tenant_id or ""), "n": body.name.strip(), "d": body.description.strip(),
                            "a": sorted(set(body.actions)), "r": sorted(set(body.resources)),
                            "p": sorted(set(body.purposes)) if body.purposes is not None else None,
                            "va": amount, "vc": body.value_currency, "md": body.max_days, "da": body.delegation_allowed,
                            "dp": body.max_depth, "ma": body.min_assurance, "rc": body.risk_class, "u": uuid.UUID(_me(caller)),
                        },
                    )
                ).one()
        except IntegrityError as e:
            raise ZidError("TEMPLATE_EXISTS", "A template with that name already exists.", http_status=409) from e
    return _view(row)


@router.post("/{template_id}:retire")
async def retire_template(template_id: uuid.UUID, request: Request, caller: CallerDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        res = await conn.execute(
            text("UPDATE authz.delegation_template SET status = 'RETIRED', retired_at = now() WHERE template_id = :id AND status = 'ACTIVE'"),
            {"id": template_id},
        )
    if res.rowcount != 1:
        raise ZidError("TEMPLATE_NOT_FOUND", "No such live template.", http_status=404)
    return {"template_id": str(template_id), "status": "RETIRED"}


@router.post("/{template_id}:apply", status_code=201)
async def apply_template(template_id: uuid.UUID, body: ApplyBody, request: Request, caller: CallerDep) -> dict[str, Any]:
    ctx = caller.ctx
    grantor = _me(caller)
    now = datetime.now(UTC)
    async with tenant_transaction(request_engine(request), ctx) as conn:
        await require_feature(
            conn,
            "advanced.advanced-delegation-workflow-templates",
            enforcement=getattr(request.app.state, "commercial_enforcement", "shadow"),
        )
        t = (await conn.execute(text("SELECT * FROM authz.delegation_template WHERE template_id = :id AND status = 'ACTIVE'"), {"id": template_id})).first()
        if t is None:
            raise ZidError("TEMPLATE_NOT_FOUND", "No such live template.", http_status=404)
        days = min(body.days or t.max_days, t.max_days)
        grant = Grant(
            grant_id=str(uuid7()),
            tenant_id=ctx.tenant_id or "",
            grant_type=GrantType.ROLE_ISSUED,
            grantor_principal_id=grantor,
            grantee_principal_id=body.grantee_principal_id,
            represented_principal_id=body.represented_principal_id,
            authority=Authority(
                actions=frozenset(t.actions),
                resources=frozenset(t.resources),
                purposes=ANY if t.purposes is None else frozenset(t.purposes),
                data=ANY,
                jurisdictions=ANY,
                tools=ANY,
                value=ValueLimit(Decimal(t.value_amount), t.value_currency) if t.value_amount is not None else None,
                count=None,
                data_bytes=None,
                min_assurance=t.min_assurance,
                window=Window(now, now + timedelta(days=days)),
            ),
            continuity_mode=ContinuityMode.PERSON_BOUND,
            status=GrantStatus.ACTIVE,
            issued_at=now,
            root_id=body.authority_root_assertion_id,
            parent_grant_id=body.parent_grant_id,
            delegation_allowed=t.delegation_allowed,
            max_delegation_depth=t.max_depth,
        )
        try:
            await issue_grant(conn, grant, risk_level=t.risk_class, now=now)
        except AuthorityError as e:
            raise http_error(e.code, str(e)) from e
    return {"authority_grant_id": grant.grant_id, "template_id": str(template_id), "valid_to": grant.authority.window.valid_to}
