"""Access requests: a person asks an owner or administrator for a permission or a spending limit.

    POST /v1/access-requests                     ask (any signed-in person; the request is theirs)
    GET  /v1/access-requests/mine                my own requests, and nobody else's
    POST /v1/access-requests/{id}:withdraw       take back my own open request
    GET  /v1/access-requests                     the company's requests (owner and administrator)
    POST /v1/access-requests/{id}:fulfil         mark it given, after the permission was issued as a grant
    POST /v1/access-requests/{id}:decline        say no, with a short note

A request grants nothing. The permission is still issued as an ordinary authority grant, through the same checks. Nobody settles their own request.
"""

from __future__ import annotations

import uuid
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from zid_db import tenant_transaction
from zid_errors import ZidError

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.protected import protected_action

router = APIRouter(prefix="/v1/access-requests", tags=["authority"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]

MAX_OPEN_PER_PERSON = 5


class AccessRequestBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    actions: list[str] = Field(min_length=1, max_length=20)
    value_amount: str | None = None
    value_currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    reason: str = Field(min_length=5, max_length=500)


class DecisionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note: str = Field(default="", max_length=300)


def _me(caller: Caller) -> uuid.UUID:
    if not caller.ctx.principal_id:
        raise ZidError("UNAUTHENTICATED", "Sign in to use access requests.", http_status=401)
    return uuid.UUID(caller.ctx.principal_id)


def _view(r: Any, *, with_requester: bool) -> dict[str, Any]:
    out: dict[str, Any] = {
        "request_id": str(r.request_id),
        "actions": list(r.actions),
        "value_limit": {"amount": str(r.value_amount), "currency": r.value_currency}
        if r.value_amount is not None
        else None,
        "reason": r.reason,
        "status": r.status,
        "decision_note": r.decision_note,
        "decided_at": r.decided_at.isoformat() if r.decided_at else None,
        "created_at": r.created_at.isoformat(),
    }
    if with_requester:  # the administrators' list says who asked; a person's own list never needs to
        out["requester_principal_id"] = str(r.requester_id)
    return out


@router.post("", status_code=201)
async def ask(body: AccessRequestBody, request: Request, caller: CallerDep) -> dict[str, Any]:
    me = _me(caller)
    if (body.value_amount is None) != (body.value_currency is None):
        raise ZidError(
            "VALIDATION_FAILED",
            "Give both an amount and its currency, or neither.",
            http_status=422,
        )
    try:
        amount = Decimal(body.value_amount) if body.value_amount is not None else None
        if amount is not None and (amount < 0 or not amount.is_finite()):
            raise ValueError
    except (InvalidOperation, ValueError) as exc:
        raise ZidError(
            "VALIDATION_FAILED", "The amount must be a non-negative number.", http_status=422
        ) from exc
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        open_now = (
            await conn.execute(
                text(
                    "SELECT count(*) FROM authz.access_request WHERE requester_id = :me AND status = 'OPEN'"
                ),
                {"me": me},
            )
        ).scalar_one()
        if open_now >= MAX_OPEN_PER_PERSON:
            raise ZidError(
                "ACCESS_REQUEST_LIMIT",
                "You already have several requests waiting. Wait for an answer, or withdraw one.",
                http_status=409,
            )
        row = (
            await conn.execute(
                text(
                    "INSERT INTO authz.access_request (request_id, tenant_id, requester_id, actions, value_amount, value_currency, reason) "
                    "VALUES (:id, :t, :me, :a, :va, :vc, :r) RETURNING *"
                ),
                {
                    "id": uuid.uuid4(),
                    "t": uuid.UUID(caller.ctx.tenant_id or ""),
                    "me": me,
                    "a": sorted(set(body.actions)),
                    "va": amount,
                    "vc": body.value_currency,
                    "r": body.reason.strip(),
                },
            )
        ).one()
    return _view(row, with_requester=False)


@router.get("/mine")
async def mine(request: Request, caller: CallerDep) -> dict[str, Any]:
    me = _me(caller)
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT * FROM authz.access_request WHERE requester_id = :me ORDER BY created_at DESC LIMIT 50"
                ),
                {"me": me},
            )
        ).all()
    return {"items": [_view(r, with_requester=False) for r in rows]}


@router.post("/{request_id}:withdraw")
async def withdraw(request_id: uuid.UUID, request: Request, caller: CallerDep) -> dict[str, Any]:
    me = _me(caller)
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        res = await conn.execute(
            text(
                "UPDATE authz.access_request SET status = 'WITHDRAWN', decided_at = now() "
                "WHERE request_id = :id AND requester_id = :me AND status = 'OPEN'"
            ),
            {"id": request_id, "me": me},
        )
    if res.rowcount != 1:  # someone else's request is reported as missing, not as theirs
        raise ZidError("ACCESS_REQUEST_NOT_FOUND", "No such open request of yours.", http_status=404)
    return {"request_id": str(request_id), "status": "WITHDRAWN"}


@router.get("")
async def list_all(
    request: Request,
    caller: CallerDep,
    status: str | None = Query(default="OPEN", pattern="^(OPEN|FULFILLED|DECLINED|WITHDRAWN)$"),
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn, caller, action="authority.grant.read", resource="authority_grant:*"
        )  # the company-wide list: not something a Member's own right covers
        rows = (
            await conn.execute(
                text(
                    "SELECT * FROM authz.access_request WHERE (CAST(:s AS text) IS NULL OR status = :s) ORDER BY created_at DESC LIMIT 200"
                ),
                {"s": status},
            )
        ).all()
    return {"items": [_view(r, with_requester=True) for r in rows]}


async def _decide(
    request: Request, caller: CallerDep, request_id: uuid.UUID, status: str, note: str
) -> dict[str, Any]:
    me = _me(caller)
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn, caller, action="authority.grant.read", resource="authority_grant:*"
        )
        row = (
            await conn.execute(
                text(
                    "SELECT requester_id, status FROM authz.access_request WHERE request_id = :id FOR UPDATE"
                ),
                {"id": request_id},
            )
        ).first()
        if row is None:
            raise ZidError("ACCESS_REQUEST_NOT_FOUND", "No such request.", http_status=404)
        if row.requester_id == me:  # nobody settles their own request
            raise ZidError(
                "ACCESS_REQUEST_OWN",
                "Someone else must answer your request.",
                http_status=403,
            )
        if row.status != "OPEN":
            raise ZidError("ACCESS_REQUEST_CLOSED", "That request was already answered.", http_status=409)
        await conn.execute(
            text(
                "UPDATE authz.access_request SET status = :s, decided_by = :me, decided_at = now(), decision_note = :n WHERE request_id = :id"
            ),
            {"s": status, "me": me, "n": note.strip() or None, "id": request_id},
        )
    return {"request_id": str(request_id), "status": status}


@router.post("/{request_id}:fulfil")
async def fulfil(
    request_id: uuid.UUID, body: DecisionBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    return await _decide(request, caller, request_id, "FULFILLED", body.note)


@router.post("/{request_id}:decline")
async def decline(
    request_id: uuid.UUID, body: DecisionBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    return await _decide(request, caller, request_id, "DECLINED", body.note)
