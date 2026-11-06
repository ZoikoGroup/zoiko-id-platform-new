"""Mandate API (ZID-API-001 §11; ZID-MANDATE-001 §9-14; MAND-06, MAND-08..12, MAND-21).

    GET  /v1/mandates                            list, newest first            (mandate.read, R1)
    GET  /v1/mandates/mine                       mandates the caller executes or created, with what
                                                 they may do to each (no permission needed: it is theirs)
    POST /v1/mandates                            grantee attenuates its grant  (mandate.create, R3;
                                                 Idempotency-Key required; created PENDING_APPROVAL)
    POST /v1/mandates/{id}/reservations          reserve capacity (Idempotency-Key required)
    POST /v1/mandate-reservations/{id}:commit    commit after the protected side effect
    POST /v1/mandate-reservations/{id}:release   release unused capacity
    GET  /v1/mandates/{id}/balance               committed/reserved/remaining (informational only)
    POST /v1/mandates/{id}:activate|:pause|:revoke
                                                 the executor or creator may pause, the creator may revoke;
                                                 anything else is `mandate.manage` (owner, administrators; CR-039)

A mandate never exceeds its source grant, and only the grant's grantee can create one (CR-010).
Only the mandate's executor may reserve against it, and only for an action and tool inside its terms.
Every ledger write runs in the cell holding the tenant's writer fence (`cell_writer_epoch`).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from zid_db import tenant_transaction
from zid_idempotency import Scope, begin, complete

from zid_platform.api_auth import (
    Caller,
    authenticated_caller,
    cell_writer_epoch,
    http_error,
    request_engine,
)
from zid_platform.modules.mandate.application.service import (
    MandateRequest,
    activate_mandate,
    create_from_grant,
)
from zid_platform.modules.mandate.domain.ledger import (
    Amounts,
    Budget,
    LedgerError,
    MandateStatus,
    Reservation,
    remaining,
)
from zid_platform.modules.mandate.domain.oversight import Move, own_moves
from zid_platform.modules.mandate.infrastructure.ledger_store import (
    commit_reservation,
    read_balance,
    release_reservation,
    reserve_capacity,
)
from zid_platform.modules.mandate.infrastructure.mandate_store import (
    MandateListing,
    creator_of,
    list_mandates,
    load_terms,
    set_mandate_status,
)
from zid_platform.modules.commercial.ports import require_feature
from zid_platform.protected import protected_action

router = APIRouter(tags=["mandate"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]


class ValueAmount(BaseModel):
    model_config = ConfigDict(extra="forbid")
    amount: str
    currency: str = Field(pattern=r"^[A-Z]{3}$")


class ReserveBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: str
    tool: str | None = None
    transaction_context: str = Field(min_length=1, max_length=4096)  # hashed; never stored in clear
    value: ValueAmount | None = None
    count: int = Field(default=0, ge=0)
    data_bytes: int = Field(default=0, ge=0)


class CommitBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    recheck_passed: bool  # the PEP's commit-time recheck result (obligation `commit_recheck`)
    execution_id: str | None = Field(default=None, max_length=128)


def _num(v: Decimal | int | None) -> str | int | None:
    return str(v) if isinstance(v, Decimal) else v


def _res_view(r: Reservation) -> dict[str, Any]:
    return {
        "reservation_id": r.reservation_id,
        "status": r.status.value,
        "expires_at": r.expires_at.isoformat(),
        "amounts": {
            "value": str(r.amounts.value),
            "count": r.amounts.count,
            "data_bytes": r.amounts.data_bytes,
        },
    }


def _ledger(e: LedgerError) -> Exception:
    return http_error(e.code, str(e))


class Delegation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    allowed: bool = False
    max_depth: int = Field(default=0, ge=0, le=16)


class MandateCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")  # no represented principal: it is the grant's
    authority_grant_id: uuid.UUID
    executor_principal_id: uuid.UUID
    purpose_id: uuid.UUID
    allowed_actions: list[str] = Field(min_length=1, max_length=64)
    resource_scopes: list[str] = Field(min_length=1, max_length=64)
    tools: list[str] = Field(default_factory=list, max_length=64)
    jurisdictions: list[str] = Field(default_factory=list, max_length=64)
    value_limit: ValueAmount | None = None
    per_transaction_value_limit: ValueAmount | None = None
    count_limit: int | None = Field(default=None, ge=0)
    data_limit_bytes: int | None = Field(default=None, ge=0)
    valid_from: datetime
    valid_to: datetime
    delegation: Delegation = Field(default_factory=Delegation)
    risk_class: int = Field(default=3, ge=0, le=3)  # the risk the source grant must resolve at


def _amount(v: ValueAmount | None) -> Decimal | None:
    if v is None:
        return None
    try:
        d = Decimal(v.amount)
        if not d.is_finite() or d < 0:
            raise InvalidOperation
    except InvalidOperation as exc:
        raise http_error(
            "MANDATE_VALUE_INVALID", "amounts must be non-negative decimal strings."
        ) from exc
    return d


def _budget(body: MandateCreate) -> Budget:
    total, per_tx = _amount(body.value_limit), _amount(body.per_transaction_value_limit)
    currencies = {v.currency for v in (body.value_limit, body.per_transaction_value_limit) if v}
    if len(currencies) > 1:
        raise http_error("MANDATE_CURRENCY_MISMATCH", "value limits must share one currency.")
    if total is not None and per_tx is not None and per_tx > total:
        raise http_error(
            "MANDATE_BUDGET_INVALID", "per_transaction_value_limit exceeds value_limit."
        )
    try:
        return Budget(
            currency=next(iter(currencies), None),
            value_limit=total,
            count_limit=body.count_limit,
            data_limit_bytes=body.data_limit_bytes,
            per_transaction_value_limit=per_tx,
        )
    except LedgerError as e:
        raise _ledger(e) from e


def _mandate_view(x: MandateListing) -> dict[str, Any]:
    t, b = x.terms, x.budget
    return {
        "mandate_id": t.mandate_id,
        "status": t.status.value,
        "authority_grant_id": x.authority_grant_id,
        "executor_principal_id": t.executor_principal_id,
        "represented_principal_id": t.represented_principal_id,
        "purpose_id": t.purpose_id,
        "allowed_actions": sorted(t.actions),
        "resource_scopes": sorted(t.resource_scope),
        "tools": sorted(t.tools),
        "jurisdictions": sorted(t.jurisdictions),
        "currency": b.currency,
        "value_limit": _num(b.value_limit),
        "per_transaction_value_limit": _num(b.per_transaction_value_limit),
        "count_limit": b.count_limit,
        "data_limit_bytes": b.data_limit_bytes,
        "valid_from": t.valid_from.isoformat(),
        "valid_to": t.valid_to.isoformat(),
        "delegation_allowed": t.delegation_allowed,
        "max_delegation_depth": t.max_delegation_depth,
        "parent_mandate_id": t.parent_mandate_id,
        "created_at": x.created_at.isoformat(),
    }


@router.get("/v1/mandates")
async def list_all(
    request: Request,
    caller: CallerDep,
    status: MandateStatus | None = None,
    executor_principal_id: uuid.UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(conn, caller, action="mandate.read", resource="mandate:*")
        items = await list_mandates(
            conn,
            status=status,
            executor_principal_id=str(executor_principal_id) if executor_principal_id else None,
            limit=limit,
        )
    return {"items": [_mandate_view(x) for x in items]}


async def _own_view(conn: Any, x: MandateListing, principal_id: str) -> dict[str, Any]:
    creator = await creator_of(conn, x.terms.mandate_id)
    moves = own_moves(principal_id, executor=x.terms.executor_principal_id, creator=creator)
    allowed = [m.value for m in (Move.PAUSE, Move.REVOKE) if m in moves]
    # the move only makes sense from the state it is in (the same table the console uses)
    legal = {
        MandateStatus.ACTIVE: {"pause", "revoke"},
        MandateStatus.PAUSED: {"revoke"},
        MandateStatus.PENDING_APPROVAL: {"revoke"},
        MandateStatus.EXHAUSTED: {"revoke"},
    }.get(x.terms.status, set())
    return {
        **_mandate_view(x),
        "i_am": [
            r
            for r, who in (("executor", x.terms.executor_principal_id), ("creator", creator))
            if who == principal_id
        ],
        "my_moves": [m for m in allowed if m in legal],
    }


@router.get("/v1/mandates/mine")
async def list_mine(
    request: Request,
    caller: CallerDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> dict[str, Any]:
    """A person's own spending authority: no company-wide permission is needed to see what is theirs."""
    me = caller.ctx.principal_id
    if not me:
        raise http_error("PRINCIPAL_REQUIRED", "Sign in to see your own mandates.")
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        items = await list_mandates(conn, involving_principal_id=me, limit=limit)
        return {"items": [await _own_view(conn, x, me) for x in items]}


@router.post("/v1/mandates", status_code=201)
async def create(
    body: MandateCreate,
    request: Request,
    caller: CallerDep,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=255)],
) -> dict[str, Any]:
    ctx = caller.ctx
    tenant = ctx.tenant_id or ""
    if not ctx.principal_id:
        raise http_error("GRANTOR_AUTHORITY_MISSING", "The authenticated caller is the grantee.")
    if body.valid_to <= body.valid_from:
        raise http_error("VALIDATION_FAILED", "valid_to must be after valid_from.")
    req = MandateRequest(
        authority_grant_id=str(body.authority_grant_id),
        executor_principal_id=str(body.executor_principal_id),
        purpose_id=str(body.purpose_id),
        actions=frozenset(body.allowed_actions),
        resource_scopes=frozenset(body.resource_scopes),
        tools=frozenset(body.tools),
        jurisdictions=frozenset(body.jurisdictions),
        budget=_budget(body),
        valid_from=body.valid_from,
        valid_to=body.valid_to,
        delegation_allowed=body.delegation.allowed,
        max_delegation_depth=body.delegation.max_depth,
    )
    scope = Scope(tenant, caller.client_id, "POST /v1/mandates")
    async with tenant_transaction(request_engine(request), ctx) as conn:
        await protected_action(conn, caller, action="mandate.create", resource="mandate:*")
        await require_feature(
            conn,
            "advanced.agent-mandates-and-scoped-delegation",
            enforcement=getattr(request.app.state, "commercial_enforcement", "shadow"),
        )
        stored = await begin(
            conn, scope=scope, key=idempotency_key, payload=body.model_dump(mode="json")
        )
        if stored is not None:
            return dict(stored.body)
        cell, epoch = await cell_writer_epoch(request, conn, tenant)
        try:
            mid = await create_from_grant(
                conn,
                req,
                tenant_id=tenant,
                creator_principal_id=ctx.principal_id,
                cell_id=cell,
                writer_epoch=epoch,
                risk_level=body.risk_class,
                now=datetime.now(UTC),
            )
        except LedgerError as e:
            raise _ledger(e) from e
        out = _mandate_view((await list_mandates(conn, mandate_id=mid, limit=1))[0])
        await complete(conn, scope=scope, key=idempotency_key, status=201, body=out)
    return out


@router.post("/v1/mandates/{mandate_id}/reservations", status_code=201)
async def reserve(
    mandate_id: uuid.UUID,
    body: ReserveBody,
    request: Request,
    caller: CallerDep,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=255)],
) -> dict[str, Any]:
    tenant = caller.ctx.tenant_id or ""
    try:
        value = Decimal(body.value.amount) if body.value else Decimal(0)
        if not value.is_finite() or value < 0:
            raise InvalidOperation
    except InvalidOperation as exc:
        raise http_error(
            "MANDATE_VALUE_INVALID", "value.amount must be a non-negative decimal string."
        ) from exc
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        try:
            terms, _, _ = await load_terms(conn, str(mandate_id))
        except LedgerError as e:
            raise _ledger(e) from e
        if terms.executor_principal_id != caller.ctx.principal_id:
            raise http_error(
                "MANDATE_EXECUTOR_MISMATCH", "Only the mandate's executor may reserve against it."
            )
        if body.action not in terms.actions:
            raise http_error("MANDATE_SCOPE_EXCEEDED", "The action is outside the mandate.")
        if body.tool is not None and body.tool not in terms.tools:
            raise http_error("MANDATE_TOOL_NOT_ALLOWED", "The tool is outside the mandate.")
        cell, epoch = await cell_writer_epoch(request, conn, tenant)
        try:
            res = await reserve_capacity(
                conn,
                tenant_id=uuid.UUID(tenant),
                mandate_id=mandate_id,
                idempotency_key=f"{caller.client_id}:{idempotency_key}",
                transaction_context=body.transaction_context,
                amounts=Amounts(value, body.count, body.data_bytes),
                currency=body.value.currency if body.value else None,
                cell_id=cell,
                writer_epoch=epoch,
                now=datetime.now(UTC),
            )
        except LedgerError as e:
            raise _ledger(e) from e
    return {"mandate_id": str(mandate_id), **_res_view(res)}


async def _settle(
    request: Request, caller: Caller, reservation_id: uuid.UUID, body: CommitBody | None
) -> dict[str, Any]:
    tenant = caller.ctx.tenant_id or ""
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        cell, epoch = await cell_writer_epoch(request, conn, tenant)
        try:
            if body is None:
                res = await release_reservation(
                    conn,
                    tenant_id=uuid.UUID(tenant),
                    reservation_id=reservation_id,
                    cell_id=cell,
                    writer_epoch=epoch,
                    now=datetime.now(UTC),
                )
            else:
                res = await commit_reservation(
                    conn,
                    tenant_id=uuid.UUID(tenant),
                    reservation_id=reservation_id,
                    cell_id=cell,
                    writer_epoch=epoch,
                    now=datetime.now(UTC),
                    recheck_passed=body.recheck_passed,
                    execution_id=body.execution_id,
                )
        except LedgerError as e:
            raise _ledger(e) from e
    return _res_view(res)


@router.post("/v1/mandate-reservations/{reservation_id}:commit")
async def commit(
    reservation_id: uuid.UUID, body: CommitBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    return await _settle(request, caller, reservation_id, body)


@router.post("/v1/mandate-reservations/{reservation_id}:release")
async def release(reservation_id: uuid.UUID, request: Request, caller: CallerDep) -> dict[str, Any]:
    return await _settle(request, caller, reservation_id, None)


@router.get("/v1/mandates/{mandate_id}/balance")
async def balance(mandate_id: uuid.UUID, request: Request, caller: CallerDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        try:
            terms, budget, _ = await load_terms(conn, str(mandate_id))
            bal = await read_balance(conn, mandate_id)
        except LedgerError as e:
            raise _ledger(e) from e

    def amounts(a: Amounts) -> dict[str, Any]:
        return {"value": str(a.value), "count": a.count, "data_bytes": a.data_bytes}

    return {
        "mandate_id": str(mandate_id),
        "status": terms.status.value,
        "currency": budget.currency,
        "committed": amounts(bal.committed),
        "reserved": amounts(bal.reserved),
        "remaining": {k: _num(v) for k, v in remaining(budget, bal).items()},
        "ledger_version": bal.ledger_version,
    }


async def _move(
    request: Request,
    caller: Caller,
    mandate_id: uuid.UUID,
    target: MandateStatus,
    risk_level: int = 3,
) -> dict[str, Any]:
    move = {
        MandateStatus.ACTIVE: Move.ACTIVATE,
        MandateStatus.PAUSED: Move.PAUSE,
        MandateStatus.REVOKED: Move.REVOKE,
    }[target]
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        try:
            terms, _, _ = await load_terms(conn, str(mandate_id))
            creator = await creator_of(conn, str(mandate_id))
        except LedgerError as e:
            raise _ledger(e) from e
        if (
            move
            not in own_moves(
                caller.ctx.principal_id, executor=terms.executor_principal_id, creator=creator
            )
        ):  # not theirs to stop: that is oversight, which the company grants to owner and administrators
            await protected_action(
                conn,
                caller,
                action="mandate.manage",
                resource="mandate:*",
                change=f"{move.value} {mandate_id}",
            )
        try:
            if target is MandateStatus.ACTIVE:  # re-resolves the source grant first (MAND-06)
                status = await activate_mandate(
                    conn,
                    tenant_id=caller.ctx.tenant_id or "",
                    mandate_id=str(mandate_id),
                    risk_level=risk_level,
                    now=datetime.now(UTC),
                )
            else:
                status = await set_mandate_status(conn, str(mandate_id), target)
        except LedgerError as e:
            raise _ledger(e) from e
    return {"mandate_id": str(mandate_id), "status": status.value}


@router.post("/v1/mandates/{mandate_id}:activate")
async def activate(
    mandate_id: uuid.UUID,
    request: Request,
    caller: CallerDep,
    risk_class: Annotated[int, Query(ge=0, le=3)] = 3,
) -> dict[str, Any]:
    return await _move(request, caller, mandate_id, MandateStatus.ACTIVE, risk_class)


@router.post("/v1/mandates/{mandate_id}:pause")
async def pause(mandate_id: uuid.UUID, request: Request, caller: CallerDep) -> dict[str, Any]:
    return await _move(request, caller, mandate_id, MandateStatus.PAUSED)


@router.post("/v1/mandates/{mandate_id}:revoke")
async def revoke(mandate_id: uuid.UUID, request: Request, caller: CallerDep) -> dict[str, Any]:
    return await _move(request, caller, mandate_id, MandateStatus.REVOKED)
