"""Two-person approval of a production credential for the company (CR-162; ZID-EXT-IDV-001 WF-05, section 05). A signed-in administrator, behind the PDP.

GET  /v1/verify-api/credential-approvals                       the company's approval requests, newest first                                          verify_api.application.read
POST /v1/verify-api/applications/{id}/credential-approvals     ask for approval to create or rotate a production credential (names the key, if any)   verify_api.application.manage
POST /v1/verify-api/credential-approvals/{id}:approve          a DIFFERENT administrator approves it                                                  verify_api.application.manage
POST /v1/verify-api/credential-approvals/{id}:reject           a DIFFERENT administrator rejects it, with a reason                                    verify_api.application.manage
POST /v1/verify-api/credential-approvals/{id}:cancel           the person who asked takes it back while it waits                                      verify_api.application.manage

The approval is spent by creating or rotating the credential with its `approval_id` (see verify_api.py), once, within 24 hours.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from zid_db import tenant_transaction
from zid_errors import ZidError

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.modules.verify_api.application import approvals_service as svc
from zid_platform.modules.verify_api.domain.model import PartnerError
from zid_platform.protected import protected_action

router = APIRouter(tags=["verify-api"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]

MANAGE = "verify_api.application.manage"
READ = "verify_api.application.read"
RESOURCE = "verify_api_application:*"

APPROVAL_STATUS = {
    "NOT_FOUND": 404,
    "APPROVAL_REQUIRED": 403,
    "APPROVAL_SELF_DECISION": 409,
    "APPROVAL_STATE_INVALID": 409,
    "APPLICATION_STATE_INVALID": 409,
    "NOTE_REQUIRED": 422,
    "VALIDATION_FAILED": 422,
}


def fail(e: PartnerError) -> ZidError:
    return ZidError(e.code, e.detail, http_status=APPROVAL_STATUS.get(e.code, 422))


class CredentialApprovalRequestBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: str = Field(max_length=8)
    credential_id: str | None = Field(default=None, max_length=64)
    public_jwk: dict[str, Any] | None = None
    certificate_pem: str | None = Field(default=None, max_length=8192)


class CredentialApprovalDecisionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note: str | None = Field(default=None, max_length=1000)


@router.get("/v1/verify-api/credential-approvals")
async def list_approvals(request: Request, caller: CallerDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(conn, caller, action=READ, resource=RESOURCE)
        return await svc.list_view(conn)


@router.post("/v1/verify-api/applications/{application_id}/credential-approvals", status_code=201)
async def request_approval(
    application_id: uuid.UUID,
    body: CredentialApprovalRequestBody,
    request: Request,
    caller: CallerDep,
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(conn, caller, action=MANAGE, resource=RESOURCE)
        try:
            return await svc.request(
                conn,
                caller.ctx.tenant_id or "",
                caller.ctx.principal_id or "",
                str(application_id),
                body.model_dump(),
            )
        except PartnerError as e:
            raise fail(e) from e


@router.post("/v1/verify-api/credential-approvals/{approval_id}:approve")
async def approve(
    approval_id: uuid.UUID,
    body: CredentialApprovalDecisionBody,
    request: Request,
    caller: CallerDep,
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(conn, caller, action=MANAGE, resource=RESOURCE)
        try:
            return await svc.approve(
                conn, str(approval_id), caller.ctx.principal_id or "", body.note
            )
        except PartnerError as e:
            raise fail(e) from e


@router.post("/v1/verify-api/credential-approvals/{approval_id}:reject")
async def reject(
    approval_id: uuid.UUID,
    body: CredentialApprovalDecisionBody,
    request: Request,
    caller: CallerDep,
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(conn, caller, action=MANAGE, resource=RESOURCE)
        try:
            return await svc.reject(
                conn, str(approval_id), caller.ctx.principal_id or "", body.note
            )
        except PartnerError as e:
            raise fail(e) from e


@router.post("/v1/verify-api/credential-approvals/{approval_id}:cancel")
async def cancel(approval_id: uuid.UUID, request: Request, caller: CallerDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(conn, caller, action=MANAGE, resource=RESOURCE)
        try:
            return await svc.cancel(conn, str(approval_id), caller.ctx.principal_id or "")
        except PartnerError as e:
            raise fail(e) from e
