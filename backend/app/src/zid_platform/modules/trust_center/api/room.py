"""The restricted evidence room, customer side (ZID-CP-WF12 v2.0, WF-12.03; CR-174). A signed-in member of a company asks for a restricted document; staff decide; the grant works only for that
person and only until it expires, is revoked or runs out of downloads. A forwarded link fails because the token is useless without the person it was issued to.

    GET  /trust/v1/room/catalogue            what can be asked for (scope only, never the file)
    POST /trust/v1/evidence-requests         ask, stating the purpose and accepting the confidentiality terms
    GET  /trust/v1/evidence-requests         the caller's own company's requests and their state
    POST /trust/v1/room/downloads            the token and the signed-in person together fetch the file; every attempt is recorded
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from zid_db import tenant_transaction
from zid_errors import ZidError

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.modules.trust_center.application import service as svc

router = APIRouter(tags=["trust-center-room"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]
STATUS = {"TERMS_REQUIRED": 422, "EVIDENCE_NOT_AVAILABLE": 404, "REQUEST_NOT_FOUND": 404}


class TrustRoomRequestIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    evidence_id: str = Field(pattern=r"^[0-9a-f-]{36}$")
    organization: str = Field(min_length=2, max_length=200)
    purpose: str = Field(min_length=10, max_length=1000)
    accept_terms: bool


class TrustRoomDownloadIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(min_length=20, max_length=200)


def _me(caller: Caller) -> tuple[str, str]:
    if not caller.ctx.principal_id or not caller.ctx.tenant_id:
        raise ZidError("UNAUTHENTICATED", "A signed-in member is required.", http_status=401)
    return str(caller.ctx.tenant_id), str(caller.ctx.principal_id)


@router.get("/trust/v1/room/catalogue")
async def catalogue(request: Request, caller: CallerDep) -> dict[str, Any]:
    _me(caller)
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        return await svc.room_catalogue(conn)


@router.post("/trust/v1/evidence-requests", status_code=201)
async def ask(body: TrustRoomRequestIn, request: Request, caller: CallerDep) -> dict[str, Any]:
    tenant, me = _me(caller)
    try:
        async with tenant_transaction(request_engine(request), caller.ctx) as conn:
            return await svc.request_access(
                conn,
                tenant=tenant,
                requester=me,
                organization=body.organization,
                purpose=body.purpose,
                evidence_id=body.evidence_id,
                accept_terms=body.accept_terms,
                now=datetime.now(UTC),
                terms_version=str(
                    getattr(request.app.state, "trust_room_terms_version", svc.ROOM_TERMS_VERSION)
                ),
            )
    except svc.TrustError as e:
        raise ZidError(e.code, e.detail or e.code, http_status=STATUS.get(e.code, 422)) from e


@router.get("/trust/v1/evidence-requests")
async def mine(request: Request, caller: CallerDep) -> dict[str, Any]:
    tenant, _ = _me(caller)
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        from zid_platform.modules.trust_center.infrastructure import store

        return {"items": [svc._request_view(r) for r in await store.requests(conn, tenant=tenant)]}


@router.post("/trust/v1/room/downloads")
async def download(body: TrustRoomDownloadIn, request: Request, caller: CallerDep) -> Response:
    tenant, me = _me(caller)
    ip = request.client.host if request.client else "unknown"
    source = hashlib.sha256(
        f"{getattr(request.app.state, 'trust_report_salt', '')}|{ip}".encode()
    ).hexdigest()
    outcome: str | None = None
    got: tuple[bytes, str, str] | None = None
    async with (
        tenant_transaction(request_engine(request), caller.ctx) as conn
    ):  # the record of a refusal must commit, so a denial is carried out of the block and raised after it
        try:
            got = await svc.room_download(
                conn,
                token=body.token,
                tenant=tenant,
                principal=me,
                now=datetime.now(UTC),
                source_hash=source,
            )
        except svc.RoomDenied as e:
            outcome = e.outcome
    if got is None:
        # an unknown token and a wrong person look the same from outside
        raise ZidError(
            "ACCESS_DENIED",
            "This access is not available.",
            http_status=403 if outcome not in ("EXPIRED",) else 410,
        )
    content, content_type, watermark = got
    return Response(
        content,
        media_type=content_type,
        headers={
            "Cache-Control": "no-store",
            "X-Robots-Tag": "noindex, noarchive",
            "Content-Disposition": 'attachment; filename="document"',
            "X-Zoiko-Watermark": watermark.encode("ascii", "ignore").decode(),
        },
    )
