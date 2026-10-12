"""Mandate API (ZID-API-001 §11; ZID-MANDATE-001 §10-14; MAND-06, MAND-08..12, MAND-21).

    POST /v1/mandates/{id}/reservations          reserve capacity (Idempotency-Key required)
    POST /v1/mandate-reservations/{id}:commit    commit after the protected side effect
    POST /v1/mandate-reservations/{id}:release   release unused capacity
    GET  /v1/mandates/{id}/balance               committed/reserved/remaining (informational only)
    POST /v1/mandates/{id}:activate|:pause|:revoke

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

from zid_platform.api_auth import (
    Caller,
    authenticated_caller,
    cell_writer_epoch,
    http_error,
    request_engine,
)
from zid_platform.modules.mandate.application.service import activate_mandate
from zid_platform.modules.mandate.domain.ledger import (
    Amounts,
    LedgerError,
    MandateStatus,
    Reservation,
    remaining,
)
from zid_platform.modules.mandate.infrastructure.ledger_store import (
    commit_reservation,
    read_balance,
    release_reservation,
    reserve_capacity,
)
from zid_platform.modules.mandate.infrastructure.mandate_store import load_terms, set_mandate_status

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
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
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
