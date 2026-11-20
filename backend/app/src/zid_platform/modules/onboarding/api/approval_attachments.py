"""Documents on an approval request over HTTP (WF-013C AT-12).

    POST /v1/organizations/{org}/approvals/{request}/attachments              attach one document (raw body)
    GET  /v1/organizations/{org}/approvals/{request}/attachments              what is attached (metadata only)
    GET  /v1/organizations/{org}/approvals/{request}/attachments/{id}         an approver opens one document

The body is the file; `Content-Type` is what the sender says (checked against the file's own bytes), `X-Attachment-Kind` says what it is
and `X-Attachment-Name` is a label. Who may attach and who may open is the approval service's own object-level answer: the requester
attaches while the request is open and unvoted; an approver (an approving role, or someone who voted) opens; everyone else is told the
request does not exist. The response is never cached and carries no URL that works for anyone else.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Request, Response
from zid_db import tenant_transaction
from zid_errors import ZidError

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.country_gate import environment_of
from zid_platform.modules.onboarding.application import approval_attachments as service
from zid_platform.modules.onboarding.infrastructure.attachment_config import attachment_policy
from zid_platform.modules.organization.ports import ProofError, proof_scanner_from_address
from zid_platform.modules.tenant.ports import case_access, check_category_transfer, load_residency

CallerDep = Annotated[Caller, Depends(authenticated_caller)]
router = APIRouter(prefix="/v1", tags=["approvals"])

PURPOSE = "APPROVAL_ATTACHMENT_STORAGE"
_STATUS = {
    "ATTACHMENT_KIND_UNKNOWN": 422,
    "ATTACHMENT_EMPTY": 422,
    "ATTACHMENT_TOO_LARGE": 413,
    "ATTACHMENT_TYPE_NOT_ALLOWED": 415,
    "ATTACHMENT_TYPE_MISMATCH": 415,
    "ATTACHMENT_ACTIVE_CONTENT": 422,
    "ATTACHMENT_MALFORMED": 422,
    "ATTACHMENT_INFECTED": 422,
    "ATTACHMENT_SCAN_UNAVAILABLE": 503,
    "ATTACHMENT_RESIDENCY_DENIED": 403,
    "ATTACHMENT_LIMIT_REACHED": 409,
    "ATTACHMENT_IDEMPOTENCY_CONFLICT": 409,
    "ATTACHMENT_CONFLICT": 409,
    "ATTACHMENT_NOT_FOUND": 404,
    "ATTACHMENT_EXPIRED": 410,
}


def _error(e: ProofError) -> ZidError:
    return ZidError(
        e.code,
        e.message,
        http_status=_STATUS.get(e.code, 422),
        retryable=e.code == "ATTACHMENT_SCAN_UNAVAILABLE",
    )


def _who(org_id: uuid.UUID, caller: Caller) -> tuple[str, str]:
    tenant, me = caller.ctx.tenant_id or "", caller.ctx.principal_id
    if not me:
        raise ZidError(
            "UNAUTHENTICATED", "An authenticated principal is required.", http_status=401
        )
    if (
        str(org_id) != tenant
    ):  # another organization's id is the same answer as one that does not exist
        raise ZidError("ORGANIZATION_NOT_FOUND", "No such organization.", http_status=404)
    return tenant, me


def _shown(a: service.AttachmentView) -> dict[str, Any]:
    return {
        "attachment_id": a.attachment_id,
        "kind": a.kind,
        "media_type": a.media_type,
        "size_bytes": a.size_bytes,
        "label": a.label,
        "scan_status": a.scan_status,
        "content_hash": a.content_hash,
        "uploaded_at": a.created_at.isoformat(),
        "access_ends_at": a.expires_at.isoformat(),
    }


def _not_found() -> ZidError:
    return ZidError("APPROVAL_CASE_NOT_FOUND", "No such request.", http_status=404)


def _scanner(request: Request) -> Any:
    injected = getattr(request.app.state, "proof_scanner", None)
    if injected is not None:
        return injected
    return proof_scanner_from_address(getattr(request.app.state, "proof_scanner_address", None))


@router.post("/organizations/{org_id}/approvals/{request_id}/attachments", status_code=201)
async def attach(
    org_id: uuid.UUID,
    request_id: uuid.UUID,
    request: Request,
    response: Response,
    caller: CallerDep,
    idempotency_key: Annotated[str, Header(min_length=8, max_length=128)],
    x_attachment_kind: Annotated[str, Header(min_length=2, max_length=64)],
    x_attachment_name: Annotated[str | None, Header(max_length=200)] = None,
) -> dict[str, Any]:
    tenant, me = _who(org_id, caller)
    policy = attachment_policy()
    too_big = ProofError(
        "ATTACHMENT_TOO_LARGE", f"The file is larger than {policy.max_bytes // (1024 * 1024)} MB."
    )
    announced = request.headers.get("content-length")
    if announced is not None and announced.isdigit() and int(announced) > policy.max_bytes:
        raise _error(too_big)
    chunks: list[bytes] = []
    size = 0
    async for (
        chunk
    ) in request.stream():  # never read more than the limit allows, whatever the headers said
        size += len(chunk)
        if size > policy.max_bytes:
            raise _error(too_big)
        chunks.append(chunk)
    content = b"".join(chunks)

    now = datetime.now(UTC)
    refusal: ProofError | None = None
    result: tuple[service.AttachmentView, bool] | None = None
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        access = await case_access(conn, str(request_id), me, now)
        if access is None:
            raise _not_found()
        if not access.may_attach:
            raise ZidError(
                "ATTACHMENT_NOT_PERMITTED",
                "Only the requester can attach documents, and only while the request is open and nobody has voted.",
                http_status=403,
            )

        async def gate() -> None:
            if await load_residency(conn, tenant) is None:
                return  # CR-134: a company with no residency profile keeps its documents in its own home cell, where this request already is; nothing leaves it, so nothing is refused
            decision = await check_category_transfer(
                conn,
                tenant,
                data_category="D3",
                destination_cell_id=str(request.app.state.home_cell),
                purpose_code=PURPOSE,
            )
            if not decision.allowed:
                raise ProofError(
                    "PROOF_RESIDENCY_DENIED",
                    "This organization's data region does not allow the document to be stored here.",
                )

        try:
            result = await service.add(
                conn, tenant, str(request_id), case_expires_at=access.expires_at, content=content,
                declared_type=request.headers.get("content-type"), kind=x_attachment_kind, filename=x_attachment_name,
                idempotency_key=idempotency_key, uploaded_by=me, policy=policy, scanner=_scanner(request),
                environment=environment_of(request), storage_gate=gate, now=now,
            )  # fmt: skip
        except ProofError as e:
            refusal = e  # leave the block normally so the audit record of the refusal is committed
    if refusal is not None:
        raise _error(refusal)
    assert result is not None
    attachment, created = result
    response.status_code = 201 if created else 200
    response.headers["Cache-Control"] = "no-store"
    return _shown(attachment)


@router.get("/organizations/{org_id}/approvals/{request_id}/attachments")
async def list_attachments(
    org_id: uuid.UUID,
    request_id: uuid.UUID,
    request: Request,
    response: Response,
    caller: CallerDep,
) -> dict[str, Any]:
    tenant, me = _who(org_id, caller)
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        access = await case_access(conn, str(request_id), me)
        if access is None:
            raise _not_found()
        items = await service.listing(conn, tenant, str(request_id))
    response.headers["Cache-Control"] = "no-store"
    return {
        "request_id": str(request_id),
        "attachments": [_shown(a) for a in items],
        "limit": attachment_policy().per_case_limit,
        "may_attach": access.may_attach,
        "may_open": access.may_open,
    }


@router.get("/organizations/{org_id}/approvals/{request_id}/attachments/{attachment_id}")
async def open_attachment(
    org_id: uuid.UUID,
    request_id: uuid.UUID,
    attachment_id: uuid.UUID,
    request: Request,
    caller: CallerDep,
) -> Response:
    tenant, me = _who(org_id, caller)
    failure: ProofError | None = None
    shown: tuple[service.AttachmentView, bytes] | None = None
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        access = await case_access(conn, str(request_id), me)
        if access is None:
            raise _not_found()
        if not access.may_open:
            raise ZidError(
                "ATTACHMENT_NOT_PERMITTED",
                "Only an approver of this request can open its documents.",
                http_status=403,
            )
        try:
            shown = await service.read(
                conn, tenant, str(request_id), str(attachment_id), reader=me, now=datetime.now(UTC)
            )
        except ProofError as e:
            failure = e
    if failure is not None:
        raise _error(failure)
    assert shown is not None
    attachment, content = shown
    return Response(
        content,
        media_type=attachment.media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{attachment.label}"',
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
            "Content-Security-Policy": "default-src 'none'; sandbox",
        },
    )
