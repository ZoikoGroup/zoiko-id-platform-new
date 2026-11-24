"""Operator plane: opening production for a Verify API partner (CR-153; ZID-EXT-IDV-001 epic E10, gate G4).

Every route is an operator action behind the operator gate (operator client, operator tenant, fresh AAL-Z3, a live grant):
GET  /operator/verify-api/go-live-requests                  every company's open requests, oldest first
POST /operator/verify-api/go-live-requests/{id}:review      take it into review (this person cannot then approve it)
POST /operator/verify-api/go-live-requests/{id}:approve     confirm each item and approve; leaves the company's production grant and its digest
POST /operator/verify-api/go-live-requests/{id}:decline     decline, with a reason the company reads
POST /operator/verify-api/grants/{tenant_id}:revoke         emergency stop: end the grant and suspend every production application at once
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from zid_db import tenant_transaction

from zid_platform.api_auth import request_engine
from zid_platform.modules.operator_access.ports import require_operator
from zid_platform.modules.verify_api.ports import (
    PartnerError,
    go_live_fail,
    go_live_service,
    operator_scope,
)

router = APIRouter(tags=["verify-api"])
OperatorDep = Annotated[Any, Depends(require_operator)]


class ApproveBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    company_verified: bool = False
    terms_signed: bool = False
    privacy_reviewed: bool = False
    billing_ready: bool = False
    provider_approved: bool = False
    note: str | None = Field(default=None, max_length=1000)


class NoteBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note: str | None = Field(default=None, max_length=1000)


@router.get("/operator/verify-api/go-live-requests")
async def open_go_live_requests(request: Request, op: OperatorDep) -> dict[str, Any]:
    async with (
        tenant_transaction(request_engine(request), request.state.operator_ctx) as conn,
        operator_scope(conn),
    ):
        return {"items": await go_live_service.operator_open(conn)}


@router.post("/operator/verify-api/go-live-requests/{request_id}:review")
async def review_go_live(
    request_id: uuid.UUID, request: Request, op: OperatorDep
) -> dict[str, Any]:
    async with (
        tenant_transaction(request_engine(request), request.state.operator_ctx) as conn,
        operator_scope(conn),
    ):
        try:
            return await go_live_service.start_review(conn, str(request_id), op.principal_id)
        except PartnerError as e:
            raise go_live_fail(e) from e


@router.post("/operator/verify-api/go-live-requests/{request_id}:approve")
async def approve_go_live(
    request_id: uuid.UUID, body: ApproveBody, request: Request, op: OperatorDep
) -> dict[str, Any]:
    async with (
        tenant_transaction(request_engine(request), request.state.operator_ctx) as conn,
        operator_scope(conn),
    ):
        try:
            return await go_live_service.approve(
                conn,
                str(request_id),
                op.principal_id,
                body.model_dump(exclude={"note"}),
                body.note,
            )
        except PartnerError as e:
            raise go_live_fail(e) from e


@router.post("/operator/verify-api/go-live-requests/{request_id}:decline")
async def decline_go_live(
    request_id: uuid.UUID, body: NoteBody, request: Request, op: OperatorDep
) -> dict[str, Any]:
    async with (
        tenant_transaction(request_engine(request), request.state.operator_ctx) as conn,
        operator_scope(conn),
    ):
        try:
            return await go_live_service.decline(conn, str(request_id), op.principal_id, body.note)
        except PartnerError as e:
            raise go_live_fail(e) from e


@router.post("/operator/verify-api/grants/{tenant_id}:revoke")
async def revoke_grant(
    tenant_id: uuid.UUID, body: NoteBody, request: Request, op: OperatorDep
) -> dict[str, Any]:
    async with (
        tenant_transaction(request_engine(request), request.state.operator_ctx) as conn,
        operator_scope(conn),
    ):
        try:
            return await go_live_service.revoke(
                conn, str(tenant_id), op.principal_id, body.note or ""
            )
        except PartnerError as e:
            raise go_live_fail(e) from e
