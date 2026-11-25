"""Verify API logos (CR-166): the company's upload, the operator's approval, and the person's page.

GET    /v1/verify-api/applications/{id}/logo           status of the logo                                      verify_api.application.read
GET    /v1/verify-api/applications/{id}/logo/image     the company's own uploaded image, whatever its status   verify_api.application.read
PUT    /v1/verify-api/applications/{id}/logo           upload (base64 JPEG or PNG); scanned and re-encoded     verify_api.application.manage
DELETE /v1/verify-api/applications/{id}/logo           remove it
GET    /v1/verify-api/hosted/{token}/logo              the person's page: the approved image, or 404 (the page shows the name as text)
Operator routes are in operator_access (`/operator/verify-api/logos`).
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from zid_db import tenant_transaction
from zid_errors import ZidError

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.modules.verification.ports import partner_capture
from zid_platform.modules.verify_api.application import logo_service as svc
from zid_platform.modules.verify_api.application import sessions_service as sessions
from zid_platform.modules.verify_api.domain.model import PartnerError
from zid_platform.modules.verify_api.infrastructure import store
from zid_platform.protected import protected_action

router = APIRouter(tags=["verify-api"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]
MANAGE, READ, RESOURCE = (
    "verify_api.application.manage",
    "verify_api.application.read",
    "verify_api_application:*",
)
STATUS = {
    "NOT_FOUND": 404,
    "CAPTURE_REFUSED": 422,
    "CAPTURE_UNAVAILABLE": 503,
    "APPLICATION_STATE_INVALID": 409,
    "LOGO_CHANGED": 409,
    "NOTE_REQUIRED": 422,
}


def fail(e: PartnerError) -> ZidError:
    s = STATUS.get(e.code, 422)
    return ZidError(e.code, e.detail, http_status=s, retryable=s >= 500)


class LogoUploadBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    image_base64: str = Field(min_length=8, max_length=(2 * 1024 * 1024 * 4) // 3 + 16)


@router.get("/v1/verify-api/applications/{application_id}/logo")
async def logo_status(
    application_id: uuid.UUID, request: Request, caller: CallerDep
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(conn, caller, action=READ, resource=RESOURCE)
        try:
            return await svc.get(conn, str(application_id))
        except PartnerError as e:
            raise fail(e) from e


@router.get("/v1/verify-api/applications/{application_id}/logo/image")
async def logo_image(application_id: uuid.UUID, request: Request, caller: CallerDep) -> Response:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(conn, caller, action=READ, resource=RESOURCE)
        try:
            img = await svc.preview(conn, str(application_id))
        except PartnerError as e:
            raise fail(e) from e
    return Response(
        img,
        media_type="image/jpeg",
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
    )


@router.put("/v1/verify-api/applications/{application_id}/logo")
async def logo_upload(
    application_id: uuid.UUID, body: LogoUploadBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    sv = partner_capture.services_from_state(request.app.state)
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(conn, caller, action=MANAGE, resource=RESOURCE)
        try:
            return await svc.upload(
                conn,
                caller.ctx.tenant_id or "",
                caller.ctx.principal_id or "",
                str(application_id),
                body.image_base64,
                sv,
            )
        except PartnerError as e:
            raise fail(e) from e


@router.delete("/v1/verify-api/applications/{application_id}/logo")
async def logo_remove(
    application_id: uuid.UUID, request: Request, caller: CallerDep
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(conn, caller, action=MANAGE, resource=RESOURCE)
        try:
            return await svc.remove(conn, str(application_id))
        except PartnerError as e:
            raise fail(e) from e


@router.get("/v1/verify-api/hosted/{token}/logo")
async def hosted_logo(token: str, request: Request) -> Response:
    """No sign-in: the link is the grant. One 404 for everything (a wrong link, no logo, a logo not yet approved), so the page just shows the name."""
    from datetime import UTC, datetime

    missing = Response(status_code=404, headers={"Cache-Control": "no-store"})
    try:
        async with request_engine(request).begin() as conn, store.auth_scope(conn):
            row = await sessions._by_link(conn, token, datetime.now(UTC))
            img = await svc.shown_image(conn, str(row["application_id"]), row["environment"])
    except PartnerError:
        return missing
    if img is None:
        return missing
    return Response(
        img,
        media_type="image/jpeg",
        headers={
            "Cache-Control": "private, max-age=300",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'",
        },
    )
