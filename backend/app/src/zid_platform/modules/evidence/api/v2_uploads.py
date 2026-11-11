"""Evidence uploads (ZID-INT-002 section 03 and 05).

    POST   /v2/evidence/uploads                      announce an upload: type, size, SHA-256 (Idempotency-Key required)
    PUT    /v2/evidence/uploads/{id}/content         send the bytes with the short-lived signed token; they are kept only if scanned CLEAN
    DELETE /v2/evidence/{evidence_ref}               erase the bytes now (the row stays as the record that it existed)

The bytes live in the cell database. A deployed cell with no configured malware scanner, or with no retention period set by the privacy
officer, refuses to accept uploads at all: it does not pretend to scan and it does not invent a retention. The manifest of an upload
(`GET /v2/evidence/{ev_ref}/manifest`) is metadata only.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, ConfigDict, Field
from zid_context import current_context
from zid_db import tenant_transaction
from zid_errors import ZidError
from zid_idempotency import Scope, begin, complete

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.modules.evidence.application import uploads as svc
from zid_platform.modules.evidence.domain import intake
from zid_platform.modules.evidence.infrastructure import upload_store as store
from zid_platform.protected import protected_action

router = APIRouter(tags=["evidence-v2"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]
IdemKey = Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)]
_STATUS = {
    "TYPE_NOT_ALLOWED": 422,
    "SIZE_NOT_ALLOWED": 422,
    "DIGEST_INVALID": 422,
    "UPLOAD_NOT_FOUND": 404,
    "UPLOAD_NOT_AWAITING_CONTENT": 409,
    "UPLOAD_TOKEN_INVALID": 403,
    "UPLOAD_TOKEN_EXPIRED": 403,
    "RETENTION_NOT_CONFIGURED": 503,
}


def _err(e: intake.UploadError) -> ZidError:
    return ZidError(
        e.code, e.code.replace("_", " ").capitalize() + ".", http_status=_STATUS.get(e.code, 422)
    )


def _ring(request: Request) -> Any:
    keyrings = getattr(request.app.state, "keyrings", None)
    if keyrings is None:
        raise ZidError("DEPENDENCY_UNAVAILABLE", "No signing key is configured.", http_status=503)
    return keyrings.credential


def _ids() -> dict[str, str]:
    ctx = current_context()
    return {
        "request_id": ctx.request_id if ctx else "req_unknown",
        "correlation_id": ctx.correlation_id if ctx else "cor_unknown",
    }


class V2UploadIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    declared_type: str = Field(min_length=3, max_length=40)
    size_bytes: int = Field(ge=1, le=intake.MAX_UPLOAD_BYTES)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    subject_ref: str | None = Field(default=None, max_length=80)


@router.post("/v2/evidence/uploads", status_code=201)
async def announce_upload(
    body: V2UploadIn, request: Request, caller: CallerDep, idempotency_key: IdemKey
) -> dict[str, Any]:
    if getattr(request.app.state, "evidence_scanner", None) is None:
        # a cell that cannot scan accepts nothing: the answer is "no", not an unscanned file
        raise ZidError(
            "DEPENDENCY_UNAVAILABLE", "No malware scanner is configured.", http_status=503
        )
    raw = (body.subject_ref or "").removeprefix("sub_")
    try:
        subject = str(uuid.UUID(raw)) if raw else str(caller.ctx.principal_id or "")
    except ValueError as e:
        raise ZidError("VALIDATION_FAILED", "A reference is not valid.", http_status=422) from e
    if not subject:
        raise ZidError("SUBJECT_REQUIRED", "A subject is required.", http_status=422)
    now = datetime.now(UTC)
    ring = _ring(request)
    scope = Scope(caller.ctx.tenant_id or "", caller.client_id, "POST /v2/evidence/uploads")
    try:
        async with tenant_transaction(request_engine(request), caller.ctx) as conn:
            stored = await begin(
                conn, scope=scope, key=idempotency_key, payload=body.model_dump(mode="json")
            )
            if stored is not None:
                return dict(stored.body)
            await protected_action(
                conn, caller, action="evidence.upload.manage", resource="evidence-upload:new"
            )
            made = await svc.announce(
                conn,
                ring,
                tenant_id=caller.ctx.tenant_id or "",
                subject=subject,
                uploaded_by=str(caller.ctx.principal_id or subject),
                declared_type=body.declared_type,
                size_bytes=body.size_bytes,
                sha256=body.sha256,
                retention_days=getattr(request.app.state, "upload_retention_days", None),
                now=now,
            )
            out = {
                **_ids(),
                "evidence_ref": f"ev_{made.upload_id}",
                "upload_url": f"/v2/evidence/uploads/{made.upload_id}/content",
                "upload_token": made.token,
                "token_expires_at": made.token_expires_at.isoformat(),
                "retained_until": made.expires_at.isoformat(),
                "max_bytes": intake.MAX_UPLOAD_BYTES,
            }
            await complete(conn, scope=scope, key=idempotency_key, status=201, body=out)
            return out
    except intake.UploadError as e:
        raise _err(e) from e


@router.put("/v2/evidence/uploads/{upload_id}/content", status_code=201)
async def send_content(
    upload_id: uuid.UUID,
    request: Request,
    caller: CallerDep,
    upload_token: Annotated[str, Header(alias="X-Upload-Token", min_length=20, max_length=4000)],
) -> dict[str, Any]:
    chunks, total = [], 0
    async for chunk in request.stream():  # never buffer more than the cap
        total += len(chunk)
        if total > intake.MAX_UPLOAD_BYTES:
            raise ZidError("SIZE_NOT_ALLOWED", "Too large.", http_status=413)
        chunks.append(chunk)
    content = b"".join(chunks)
    now = datetime.now(UTC)
    uid = str(upload_id)
    tenant = caller.ctx.tenant_id or ""
    try:
        svc.check_token(_ring(request), upload_token, upload_id=uid, tenant_id=tenant, now=now)
        async with tenant_transaction(request_engine(request), caller.ctx) as conn:
            await protected_action(
                conn, caller, action="evidence.upload.manage", resource=f"evidence-upload:{uid}"
            )
            result = await svc.receive(
                conn, getattr(request.app.state, "evidence_scanner", None), uid, content, now
            )
            row = await store.load(conn, uid)
    except intake.UploadError as e:
        raise _err(e) from e
    # raised only AFTER the transaction committed, so the recorded refusal (and its reason) survives
    if result.state == "REJECTED":
        raise ZidError(
            f"EVIDENCE_REJECTED_{result.reason}",
            f"The upload was rejected: {(result.reason or '').lower().replace('_', ' ')}.",
            http_status=422,
        )
    return {**_ids(), **svc.manifest(row or {})}


@router.delete("/v2/evidence/{evidence_ref}")
async def delete_evidence(evidence_ref: str, request: Request, caller: CallerDep) -> dict[str, Any]:
    raw = evidence_ref.removeprefix("ev_")
    try:
        uid = str(uuid.UUID(raw))
    except ValueError as e:
        raise ZidError("VALIDATION_FAILED", "A reference is not valid.", http_status=422) from e
    now = datetime.now(UTC)
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn, caller, action="evidence.upload.manage", resource=f"evidence-upload:{uid}"
        )
        if await store.load(conn, uid) is None:
            raise ZidError("NOT_FOUND", "Not found.", http_status=404)
        if not await svc.delete(conn, uid, now):
            raise ZidError("INCOMPATIBLE_STATE", "Nothing is held to delete.", http_status=409)
        row = await store.load(conn, uid)
    return {**_ids(), **svc.manifest(row or {})}
