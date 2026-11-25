"""Operator plane: opening production for a Verify API partner (CR-153; ZID-EXT-IDV-001 epic E10, gate G4).

Every route is an operator action behind the operator gate (operator client, operator tenant, fresh AAL-Z3, a live grant):
GET  /operator/verify-api/go-live-requests                  every company's open requests, oldest first
POST /operator/verify-api/go-live-requests/{id}:review      take it into review (this person cannot then approve it)
POST /operator/verify-api/go-live-requests/{id}:approve     confirm each item and approve; leaves the company's production grant and its digest
POST /operator/verify-api/go-live-requests/{id}:decline     decline, with a reason the company reads
POST /operator/verify-api/grants/{tenant_id}:revoke         emergency stop: end the grant and suspend every production application at once
GET  /operator/verify-api/cases                             every company's open disputes, appeals and erasure requests (CR-159)
POST /operator/verify-api/cases/{id}:review | :resolve | :reject   review, then a DIFFERENT operator resolves (an erasure is carried out) or rejects, with a reason the company reads
GET  /operator/verify-api/prices                            the price list: proposed, approved and rejected prices of a verification's usage facts (CR-161)
POST /operator/verify-api/prices                            propose a price from the first day of a later month
POST /operator/verify-api/prices/{id}:approve | :reject     a DIFFERENT operator approves or rejects it
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
    case_fail,
    cases_service,
    go_live_fail,
    go_live_service,
    logo_service,
    operator_scope,
    pricing_service,
    review_service,
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
                sign=getattr(request.app.state, "verify_go_live_sign", None),
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


@router.get("/operator/verify-api/cases")
async def open_cases(request: Request, op: OperatorDep) -> dict[str, Any]:
    async with (
        tenant_transaction(request_engine(request), request.state.operator_ctx) as conn,
        operator_scope(conn),
    ):
        return {"items": await cases_service.operator_open(conn)}


@router.post("/operator/verify-api/cases/{case_id}:review")
async def review_case(case_id: uuid.UUID, request: Request, op: OperatorDep) -> dict[str, Any]:
    async with (
        tenant_transaction(request_engine(request), request.state.operator_ctx) as conn,
        operator_scope(conn),
    ):
        try:
            return await cases_service.start_review(conn, str(case_id), op.principal_id)
        except PartnerError as e:
            raise case_fail(e) from e


@router.post("/operator/verify-api/cases/{case_id}:resolve")
async def resolve_case(
    case_id: uuid.UUID, body: NoteBody, request: Request, op: OperatorDep
) -> dict[str, Any]:
    async with (
        tenant_transaction(request_engine(request), request.state.operator_ctx) as conn,
        operator_scope(conn),
    ):
        try:
            return await cases_service.resolve(conn, str(case_id), op.principal_id, body.note)
        except PartnerError as e:
            raise case_fail(e) from e


@router.post("/operator/verify-api/cases/{case_id}:reject")
async def reject_case(
    case_id: uuid.UUID, body: NoteBody, request: Request, op: OperatorDep
) -> dict[str, Any]:
    async with (
        tenant_transaction(request_engine(request), request.state.operator_ctx) as conn,
        operator_scope(conn),
    ):
        try:
            return await cases_service.reject(conn, str(case_id), op.principal_id, body.note)
        except PartnerError as e:
            raise case_fail(e) from e


class VerifyPriceBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    meter: str = Field(max_length=40)
    currency: str = Field(max_length=3)
    unit_rate: str = Field(max_length=24)  # a decimal string, never a float
    effective_from: str = Field(max_length=10)


@router.get("/operator/verify-api/prices")
async def list_prices(request: Request, op: OperatorDep) -> dict[str, Any]:
    async with (
        tenant_transaction(request_engine(request), request.state.operator_ctx) as conn,
        operator_scope(conn),
    ):
        return await pricing_service.list_view(conn)


@router.post("/operator/verify-api/prices", status_code=201)
async def propose_price(body: VerifyPriceBody, request: Request, op: OperatorDep) -> dict[str, Any]:
    async with (
        tenant_transaction(request_engine(request), request.state.operator_ctx) as conn,
        operator_scope(conn),
    ):
        try:
            return await pricing_service.propose(conn, op.principal_id, body.model_dump())
        except PartnerError as e:
            raise go_live_fail(e) from e


@router.post("/operator/verify-api/prices/{price_id}:approve")
async def approve_price(
    price_id: uuid.UUID, body: NoteBody, request: Request, op: OperatorDep
) -> dict[str, Any]:
    async with (
        tenant_transaction(request_engine(request), request.state.operator_ctx) as conn,
        operator_scope(conn),
    ):
        try:
            return await pricing_service.approve(conn, str(price_id), op.principal_id, body.note)
        except PartnerError as e:
            raise go_live_fail(e) from e


@router.post("/operator/verify-api/prices/{price_id}:reject")
async def reject_price(
    price_id: uuid.UUID, body: NoteBody, request: Request, op: OperatorDep
) -> dict[str, Any]:
    async with (
        tenant_transaction(request_engine(request), request.state.operator_ctx) as conn,
        operator_scope(conn),
    ):
        try:
            return await pricing_service.reject(conn, str(price_id), op.principal_id, body.note)
        except PartnerError as e:
            raise go_live_fail(e) from e


class VerifyLogoDecisionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    digest: str = Field(
        min_length=64, max_length=64
    )  # the digest of the image the operator looked at
    note: str | None = Field(default=None, max_length=1000)


@router.get("/operator/verify-api/logos")
async def pending_logos(request: Request, op: OperatorDep) -> dict[str, Any]:
    async with (
        tenant_transaction(request_engine(request), request.state.operator_ctx) as conn,
        operator_scope(conn),
    ):
        return {"items": await logo_service.operator_pending(conn)}


@router.post("/operator/verify-api/logos/{application_id}:approve")
@router.post("/operator/verify-api/logos/{application_id}:reject")
async def decide_logo(
    application_id: uuid.UUID, body: VerifyLogoDecisionBody, request: Request, op: OperatorDep
) -> dict[str, Any]:
    approve = request.url.path.endswith(":approve")
    async with (
        tenant_transaction(request_engine(request), request.state.operator_ctx) as conn,
        operator_scope(conn),
    ):
        try:
            return await logo_service.operator_decide(
                conn, str(application_id), body.digest, approve, op.principal_id, body.note
            )
        except PartnerError as e:
            raise go_live_fail(e) from e


class VerifyHoldBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason_code: str | None = Field(default=None, max_length=60)
    note: str | None = Field(default=None, max_length=1000)


@router.get("/operator/verify-api/review-cases")
async def open_review_cases(request: Request, op: OperatorDep) -> dict[str, Any]:
    """Every company's open review cases, soonest due first, with whether a regulatory hold is in force. Case content and evidence are not here."""
    async with (
        tenant_transaction(request_engine(request), request.state.operator_ctx) as conn,
        operator_scope(conn),
    ):
        return {"items": await review_service.operator_open(conn)}


@router.post("/operator/verify-api/review-cases/{case_id}:hold")
async def hold_review_case(
    case_id: str, body: VerifyHoldBody, request: Request, op: OperatorDep
) -> dict[str, Any]:
    """Place a regulatory hold: every change and finality stops on the case, and no company reviewer can lift it."""
    async with (
        tenant_transaction(request_engine(request), request.state.operator_ctx) as conn,
        operator_scope(conn),
    ):
        try:
            return await review_service.place_hold(
                conn, case_id, op.principal_id, body.reason_code or ""
            )
        except PartnerError as e:
            raise go_live_fail(e) from e


@router.post("/operator/verify-api/review-cases/{case_id}:release-hold")
async def release_review_hold(
    case_id: str, body: VerifyHoldBody, request: Request, op: OperatorDep
) -> dict[str, Any]:
    """A DIFFERENT operator from the one who placed the hold releases it, with a reason."""
    async with (
        tenant_transaction(request_engine(request), request.state.operator_ctx) as conn,
        operator_scope(conn),
    ):
        try:
            return await review_service.release_hold(conn, case_id, op.principal_id, body.note)
        except PartnerError as e:
            raise go_live_fail(e) from e
