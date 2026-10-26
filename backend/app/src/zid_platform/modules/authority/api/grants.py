"""Authority grant API (ZID-API-001 §10; ZID-MANDATE-001 §2.1, §6, §21).

    GET  /v1/authority-grants                  list, newest first     (authority.grant.read, R1)
    POST /v1/authority-grants                  create (root-backed or delegated) after attenuation checks
    GET  /v1/authority-grants/{id}             read
    GET  /v1/authority-grants/{id}/chain       chain summary (ids and commitments only)
    POST /v1/authority-grants/{id}:revoke      terminal revoke; descendants stop resolving at once

The grantor is always the authenticated principal: a caller can never self-certify someone else's
authority (MANDATE §2.1). Creation is idempotent per Idempotency-Key (API §1.1).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from zid_app.pagination import InvalidCursor, decode_cursor, page_of
from zid_context import uuid7
from zid_db import tenant_transaction
from zid_errors import ZidError
from zid_idempotency import Scope, begin, complete

from zid_platform.api_auth import Caller, authenticated_caller, http_error, request_engine
from zid_platform.modules.authority.application.service import issue_grant, resolve, revoke_grant
from zid_platform.modules.authority.domain.algebra import ANY, Authority, ValueLimit, Window
from zid_platform.modules.authority.domain.grants import (
    AuthorityError,
    ContinuityMode,
    Grant,
    GrantStatus,
    GrantType,
)
from zid_platform.modules.authority.infrastructure.grant_store import list_grants, load_chain
from zid_platform.protected import protected_action

router = APIRouter(prefix="/v1/authority-grants", tags=["authority"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]


class Money(BaseModel):
    model_config = ConfigDict(extra="forbid")
    amount: str
    currency: str = Field(pattern=r"^[A-Z]{3}$")


class Delegation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    allowed: bool = False
    max_depth: int = Field(default=0, ge=0, le=16)


class GrantCreate(BaseModel):
    model_config = ConfigDict(
        extra="forbid"
    )  # no is_admin-style shortcuts, no tenant field (API §10.1)
    grantee_principal_id: str
    represented_principal_id: str
    grant_type: GrantType = GrantType.ROLE_ISSUED
    continuity_mode: ContinuityMode = ContinuityMode.PERSON_BOUND  # DEC-C2: default PERSON_BOUND
    authority_root_assertion_id: str | None = None
    parent_grant_id: str | None = None
    actions: list[str] = Field(min_length=1)
    resources: list[str] = Field(min_length=1)
    purposes: list[str] | None = None
    jurisdictions: list[str] | None = None
    tools: list[str] | None = None
    data_categories: list[str] | None = None
    value_limit: Money | None = None
    count_limit: int | None = Field(default=None, ge=0)
    data_limit_bytes: int | None = Field(default=None, ge=0)
    min_assurance: int = Field(default=0, ge=0, le=3)
    valid_from: datetime
    valid_to: datetime
    delegation: Delegation = Field(default_factory=Delegation)
    risk_class: int = Field(
        default=3, ge=0, le=3
    )  # the risk the grant must be usable at (root eligibility)


def _set(v: list[str] | None) -> Any:
    return ANY if v is None else frozenset(v)


def _view(g: Grant) -> dict[str, Any]:
    a = g.authority
    return {
        "authority_grant_id": g.grant_id,
        "status": g.status.value,
        "grant_type": g.grant_type.value,
        "continuity_mode": g.continuity_mode.value,
        "grantor_principal_id": g.grantor_principal_id,
        "grantee_principal_id": g.grantee_principal_id,
        "represented_principal_id": g.represented_principal_id,
        "authority_root_assertion_id": g.root_id,
        "parent_grant_id": g.parent_grant_id,
        "actions": None if a.actions is ANY else sorted(a.actions),  # type: ignore[arg-type]
        "resources": None if a.resources is ANY else sorted(a.resources),  # type: ignore[arg-type]
        "value_limit": {"amount": str(a.value.amount), "currency": a.value.currency}
        if a.value
        else None,
        "valid_from": a.window.valid_from,
        "valid_to": a.window.valid_to,
        "delegation": {"allowed": g.delegation_allowed, "max_depth": g.max_delegation_depth},
        "revision": g.revision,
    }


@router.post("", status_code=201)
async def create_grant(
    body: GrantCreate,
    request: Request,
    caller: CallerDep,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> dict[str, Any]:
    ctx = caller.ctx
    if not ctx.principal_id:
        raise http_error("GRANTOR_AUTHORITY_MISSING", "The authenticated caller is the grantor.")
    try:
        value = (
            ValueLimit(Decimal(body.value_limit.amount), body.value_limit.currency)
            if body.value_limit
            else None
        )
    except (InvalidOperation, ValueError) as exc:
        raise http_error(
            "VALIDATION_FAILED", "value_limit.amount must be a decimal string."
        ) from exc
    now = datetime.now(UTC)
    grant = Grant(
        grant_id=str(uuid7()),
        tenant_id=ctx.tenant_id or "",
        grant_type=body.grant_type,
        grantor_principal_id=ctx.principal_id,
        grantee_principal_id=body.grantee_principal_id,
        represented_principal_id=body.represented_principal_id,
        authority=Authority(
            actions=frozenset(body.actions),
            resources=frozenset(body.resources),
            purposes=_set(body.purposes),
            data=_set(body.data_categories),
            jurisdictions=_set(body.jurisdictions),
            tools=_set(body.tools),
            value=value,
            count=body.count_limit,
            data_bytes=body.data_limit_bytes,
            min_assurance=body.min_assurance,
            window=Window(body.valid_from, body.valid_to),
        ),
        continuity_mode=body.continuity_mode,
        status=GrantStatus.ACTIVE,
        issued_at=now,
        root_id=body.authority_root_assertion_id,
        parent_grant_id=body.parent_grant_id,
        delegation_allowed=body.delegation.allowed,
        max_delegation_depth=body.delegation.max_depth,
    )
    scope = Scope(ctx.tenant_id or "", caller.client_id, "POST /v1/authority-grants")
    async with tenant_transaction(request_engine(request), ctx) as conn:
        stored = await begin(
            conn, scope=scope, key=idempotency_key, payload=body.model_dump(mode="json")
        )
        if stored is not None:
            return dict(stored.body)
        try:
            await issue_grant(conn, grant, risk_level=body.risk_class, now=now)
        except AuthorityError as e:
            raise http_error(e.code, str(e)) from e
        out = _view(grant)
        await complete(conn, scope=scope, key=idempotency_key, status=201, body=out)
    return out


@router.get("")
async def list_all(
    request: Request,
    caller: CallerDep,
    status: GrantStatus | None = None,
    grantee_principal_id: uuid.UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
) -> dict[str, Any]:
    """Newest first. `next_cursor` (when present) continues the list after the last row of this page."""
    scope = f"grants|{status.value if status else '*'}|{grantee_principal_id or '*'}"
    try:
        after = decode_cursor(cursor, scope) if cursor else None
        after_key = (datetime.fromisoformat(after[0]), after[1]) if after else None
        if after_key:
            uuid.UUID(after_key[1])
    except (InvalidCursor, ValueError) as e:
        raise ZidError(
            "INVALID_CURSOR", "The cursor is not valid for this list.", http_status=400
        ) from e
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn, caller, action="authority.grant.read", resource="authority_grant:*"
        )
        grants = await list_grants(
            conn,
            status=status,
            grantee_principal_id=str(grantee_principal_id) if grantee_principal_id else None,
            limit=limit + 1,
            after=after_key,
        )
    items, next_cursor = page_of(
        grants, limit, lambda g: (g.issued_at.isoformat(), g.grant_id), scope
    )
    return {"items": [_view(g) for g in items], "next_cursor": next_cursor}


@router.get("/{grant_id}")
async def read_grant(grant_id: uuid.UUID, request: Request, caller: CallerDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        chain = await load_chain(conn, str(grant_id))
    if str(grant_id) not in chain:
        raise http_error("AUTHORITY_GRANT_NOT_FOUND")
    return _view(chain[str(grant_id)])


@router.get("/{grant_id}/chain")
async def read_chain(
    grant_id: uuid.UUID,
    request: Request,
    caller: CallerDep,
    risk_class: Annotated[int, Query(ge=0, le=3)] = 1,
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        res = await resolve(
            conn,
            tenant_id=caller.ctx.tenant_id or "",
            grant_id=str(grant_id),
            risk_level=risk_class,
            now=datetime.now(UTC),
        )
    return {
        "valid": res.valid,
        "chain_ids": list(res.chain_ids),
        "authority_root_assertion_id": res.root_id,
        "codes": list(res.codes),
    }


@router.post("/{grant_id}:revoke")
async def revoke(grant_id: uuid.UUID, request: Request, caller: CallerDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        try:
            g = await revoke_grant(conn, str(grant_id))
        except AuthorityError as e:
            code = "AUTHORITY_GRANT_NOT_FOUND" if e.code == "AUTHORITY_MISSING" else e.code
            raise http_error(code, str(e)) from e
    return {"authority_grant_id": g.grant_id, "status": g.status.value}
