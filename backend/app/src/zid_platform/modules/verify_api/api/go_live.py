"""Verify API: asking to open production (CR-153; ZID-EXT-IDV-001 epic E10). A signed-in administrator, behind the PDP.

GET  /v1/verify-api/go-live                          the automatic sandbox checks now, the latest request, the production grant, the choices   verify_api.application.read
POST /v1/verify-api/go-live-requests                 ask to open production (terms accepted, sandbox certified)                             verify_api.application.manage
POST /v1/verify-api/go-live-requests/{id}:withdraw   take an open request back                                                              verify_api.application.manage
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from zid_db import tenant_transaction
from zid_errors import ZidError

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.modules.verify_api.application import go_live_service as svc
from zid_platform.modules.verify_api.domain.model import PartnerError
from zid_platform.protected import protected_action

router = APIRouter(tags=["verify-api"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]

MANAGE = "verify_api.application.manage"
READ = "verify_api.application.read"
RESOURCE = "verify_api_application:*"

GO_LIVE_STATUS = {
    "NOT_FOUND": 404,
    "GO_LIVE_NOT_READY": 409,
    "GO_LIVE_SUITE_NOT_PASSED": 409,
    "GO_LIVE_COMPANY_NOT_VERIFIED": 409,
    "PRODUCTION_REQUIREMENTS_NOT_MET": 409,
    "GO_LIVE_SELF_SERVE_OFF": 409,
    "GO_LIVE_SIGNING_UNAVAILABLE": 503,
    "PRICE_NOT_IN_FUTURE": 422,
    "PRICE_STATE_INVALID": 409,
    "PRICE_SELF_APPROVAL": 409,
    "PRICE_EXISTS": 409,
    "LOGO_CHANGED": 409,
    "REGULATORY_HOLD": 423,
    "HOLD_NOT_RELEASABLE": 409,
    "CASE_OPEN": 409,
    "CASE_NOT_FOUND": 404,
    "GO_LIVE_REQUEST_OPEN": 409,
    "GO_LIVE_ALREADY_APPROVED": 409,
    "GO_LIVE_STATE_INVALID": 409,
    "GO_LIVE_SELF_APPROVAL": 403,
    "TERMS_NOT_ACCEPTED": 422,
    "ATTESTATION_MISSING": 422,
    "NOTE_REQUIRED": 422,
    "VALIDATION_FAILED": 422,
}


def fail(e: PartnerError) -> ZidError:
    return ZidError(e.code, e.detail, http_status=GO_LIVE_STATUS.get(e.code, 422))


class GoLiveBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    markets: list[str] = Field(min_length=1, max_length=20)
    methods: list[str] = Field(min_length=1, max_length=8)
    purpose_code: str = Field(max_length=64)
    volume_band: str = Field(max_length=16)
    use_case: str = Field(max_length=600)
    terms_version: str = Field(max_length=16)
    dpa_version: str = Field(max_length=16)
    accept_terms: bool
    accept_dpa: bool


@router.get("/v1/verify-api/go-live")
async def go_live_status(request: Request, caller: CallerDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(conn, caller, action=READ, resource=RESOURCE)
        return await svc.status_view(
            conn, caller.ctx.tenant_id or "", caller.ctx.principal_id or None
        )


@router.post("/v1/verify-api/go-live-requests", status_code=201)
async def ask_to_go_live(body: GoLiveBody, request: Request, caller: CallerDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(conn, caller, action=MANAGE, resource=RESOURCE)
        try:
            if svc.self_serve_enabled():
                # Production opens by itself when the person's identity, the company and a paid plan are all complete; otherwise it says which is missing. No operator, no sandbox test.
                return await svc.open_production(
                    conn,
                    caller.ctx.tenant_id or "",
                    caller.ctx.principal_id or "",
                    body.model_dump(),
                    sign=getattr(request.app.state, "verify_go_live_sign", None),
                )
            return await svc.submit(
                conn, caller.ctx.tenant_id or "", caller.ctx.principal_id or "", body.model_dump()
            )
        except PartnerError as e:
            raise fail(e) from e


@router.post("/v1/verify-api/go-live-requests/{request_id}:withdraw")
async def withdraw_go_live(
    request_id: uuid.UUID, request: Request, caller: CallerDep
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(conn, caller, action=MANAGE, resource=RESOURCE)
        try:
            return await svc.withdraw(conn, str(request_id))
        except PartnerError as e:
            raise fail(e) from e
