"""Verify API disputes, appeals and erasure requests for the company (CR-159; ZID-EXT-IDV-001 WF-14, WF-16, epic E18). A signed-in administrator, behind the PDP.

GET  /v1/verify-api/cases                     the company's cases, newest first                                        verify_api.application.read
POST /v1/verify-api/cases                     raise a dispute or appeal about one session, or an erasure of one customer reference   verify_api.application.manage
POST /v1/verify-api/cases/{id}:withdraw       withdraw a case that has not been decided                                 verify_api.application.manage

Zoiko iD's own side (take into review, resolve, reject) is under /operator/verify-api/cases.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from zid_db import tenant_transaction
from zid_errors import ZidError

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.modules.verify_api.application import cases_service as svc
from zid_platform.modules.verify_api.domain.model import PartnerError
from zid_platform.protected import protected_action

router = APIRouter(tags=["verify-api"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]

MANAGE = "verify_api.application.manage"
READ = "verify_api.application.read"
RESOURCE = "verify_api_application:*"

CASE_STATUS = {
    "NOT_FOUND": 404,
    "CASE_STATE_INVALID": 409,
    "CASE_NOT_ALLOWED": 409,
    "CASE_OPEN": 409,
    "CASE_SELF_RESOLVE": 409,
    "NOTE_REQUIRED": 422,
    "VALIDATION_FAILED": 422,
}


def fail(e: PartnerError) -> ZidError:
    return ZidError(e.code, e.detail, http_status=CASE_STATUS.get(e.code, 422))


class VerifyCaseBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str = Field(max_length=12)
    reason: str = Field(max_length=1200)
    session_id: str | None = Field(default=None, max_length=64)
    customer_reference: str | None = Field(default=None, max_length=128)


@router.get("/v1/verify-api/cases")
async def list_cases(request: Request, caller: CallerDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(conn, caller, action=READ, resource=RESOURCE)
        return await svc.list_cases(conn)


@router.post("/v1/verify-api/cases", status_code=201)
async def raise_case(body: VerifyCaseBody, request: Request, caller: CallerDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(conn, caller, action=MANAGE, resource=RESOURCE)
        try:
            return await svc.raise_case(
                conn, caller.ctx.tenant_id or "", caller.ctx.principal_id or "", body.model_dump()
            )
        except PartnerError as e:
            raise fail(e) from e


@router.post("/v1/verify-api/cases/{case_id}:withdraw")
async def withdraw_case(case_id: uuid.UUID, request: Request, caller: CallerDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(conn, caller, action=MANAGE, resource=RESOURCE)
        try:
            return await svc.withdraw(conn, str(case_id))
        except PartnerError as e:
            raise fail(e) from e
