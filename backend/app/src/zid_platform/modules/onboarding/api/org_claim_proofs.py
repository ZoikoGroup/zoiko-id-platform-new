"""Proof documents for a company claim over HTTP (WF-13B section 06, AT-12).

    POST /v1/org-claims/{id}/proofs                       upload one document (raw body)      (b.upload-organization-kyb-evidence)
    GET  /v1/org-claims/{id}/proofs                       what has been uploaded (metadata only)
    GET  /v1/reviews/kyb-cases/{id}/proofs/{proof_id}     a reviewer opens one document        (organization.kyb.review)

The body is the file itself; `Content-Type` is what the sender says it is (checked against the file's own bytes), `X-Proof-Kind` says
what the document is and `X-Proof-Name` is a label. An applicant never gets a document back: only a reviewer with the protected action
does, and every read is audited. A refused file leaves an audit record and nothing else (the record is committed before the answer).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any, NoReturn

from fastapi import APIRouter, Depends, Header, Request, Response
from zid_db import tenant_transaction
from zid_errors import ZidError

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.country_gate import environment_of
from zid_platform.modules.onboarding.api.org_claims import _kyb_error, _me, _require_admin
from zid_platform.modules.organization.ports import (
    KybError,
    ProofError,
    ScanStatus,
    certificate_read,
    proof_policy,
    proof_scanner_from_address,
    proof_service,
)
from zid_platform.modules.tenant.ports import Mode, check_category_transfer, matrix_gate
from zid_platform.modules.verification.ports import ImagingUnavailable, read_page_text
from zid_platform.protected import protected_action

CallerDep = Annotated[Caller, Depends(authenticated_caller)]
router = APIRouter(prefix="/v1", tags=["org-claims"])

_STATUS = {
    "PROOF_KIND_UNKNOWN": 422,
    "PROOF_EMPTY": 422,
    "PROOF_TOO_LARGE": 413,
    "PROOF_TYPE_NOT_ALLOWED": 415,
    "PROOF_TYPE_MISMATCH": 415,
    "PROOF_ACTIVE_CONTENT": 422,
    "PROOF_MALFORMED": 422,
    "PROOF_INFECTED": 422,
    "PROOF_SCAN_UNAVAILABLE": 503,
    "PROOF_RESIDENCY_DENIED": 403,
    "PROOF_LIMIT_REACHED": 409,
    "PROOF_IDEMPOTENCY_CONFLICT": 409,
    "PROOF_CONFLICT": 409,
    "PROOF_NOT_FOUND": 404,
}
PURPOSE = "KYB_PROOF_STORAGE"
READABLE_KINDS = frozenset({"CERTIFICATE_OF_INCORPORATION", "REGISTER_EXTRACT"})


def _error(e: ProofError) -> ZidError:
    return ZidError(
        e.code,
        e.message,
        http_status=_STATUS.get(e.code, 422),
        retryable=e.code == "PROOF_SCAN_UNAVAILABLE",
    )


async def _unreachable(
    request: Request, caller: Caller, case_id: uuid.UUID, attempt: str, e: KybError
) -> NoReturn:
    """T12: audit a denied attempt on a claim the caller cannot see, in its own transaction (the refused one ends in an error). The
    answer to the caller stays the plain 404 it always was."""
    if e.code == "CASE_NOT_FOUND" and caller.ctx.principal_id:
        async with tenant_transaction(request_engine(request), caller.ctx) as conn:
            await proof_service.record_case_unreachable(
                conn,
                caller.ctx.tenant_id or "",
                case_id=str(case_id),
                actor=caller.ctx.principal_id,
                attempt=attempt,
                now=datetime.now(UTC),
            )
    raise _kyb_error(e) from e


def _shown(p: proof_service.ProofView) -> dict[str, Any]:
    return {
        "proof_id": p.proof_id,
        "kind": p.kind,
        "media_type": p.media_type,
        "size_bytes": p.size_bytes,
        "label": p.label,
        "scan_status": p.scan_status,
        "content_hash": p.content_hash,
        "uploaded_at": p.created_at.isoformat(),
    }


def _scanner(request: Request) -> Any:
    injected = getattr(request.app.state, "proof_scanner", None)
    if injected is not None:
        return injected
    return proof_scanner_from_address(getattr(request.app.state, "proof_scanner_address", None))


def _environment(request: Request) -> str:
    return environment_of(request)


@router.post(
    "/org-claims/{claim_id}/proofs",
    status_code=201,
    dependencies=[Depends(matrix_gate("b.upload-organization-kyb-evidence", Mode.EXECUTE))],
)
async def upload_proof(
    claim_id: uuid.UUID,
    request: Request,
    response: Response,
    caller: CallerDep,
    idempotency_key: Annotated[str, Header(min_length=8, max_length=128)],
    x_proof_kind: Annotated[str, Header(min_length=2, max_length=64)],
    x_proof_name: Annotated[str | None, Header(max_length=200)] = None,
) -> dict[str, Any]:
    policy = proof_policy()
    declared = request.headers.get("content-type")
    announced = request.headers.get("content-length")
    if announced is not None and announced.isdigit() and int(announced) > policy.max_bytes:
        raise _error(
            ProofError(
                "PROOF_TOO_LARGE",
                f"The file is larger than {policy.max_bytes // (1024 * 1024)} MB.",
            )
        )
    chunks: list[bytes] = []
    size = 0
    async for (
        chunk
    ) in request.stream():  # never read more than the limit allows, whatever the headers said
        size += len(chunk)
        if size > policy.max_bytes:
            raise _error(
                ProofError(
                    "PROOF_TOO_LARGE",
                    f"The file is larger than {policy.max_bytes // (1024 * 1024)} MB.",
                )
            )
        chunks.append(chunk)
    content = b"".join(chunks)

    now = datetime.now(UTC)
    tenant = caller.ctx.tenant_id or ""
    refusal: ProofError | None = None
    missing: KybError | None = None
    result: tuple[proof_service.ProofView, bool] | None = None
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        me = await _require_admin(conn, caller)

        async def gate() -> None:
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
            result = await proof_service.add_proof(
                conn, tenant, str(claim_id), content=content, declared_type=declared, kind=x_proof_kind, filename=x_proof_name,
                idempotency_key=idempotency_key, uploaded_by=me, policy=policy, scanner=_scanner(request),
                environment=_environment(request), storage_gate=gate, now=now,
            )  # fmt: skip
        except ProofError as e:
            refusal = e  # leave the block normally so the audit record of the refusal is committed
        except KybError as e:
            missing = e  # answered below, so the record of a refused attempt can commit
    if missing is not None:
        await _unreachable(request, caller, claim_id, "upload", missing)
    if refusal is not None:
        raise _error(refusal)
    assert result is not None
    proof, created = result
    response.status_code = 201 if created else 200
    shown = _shown(proof)
    if proof.kind in READABLE_KINDS:
        shown["read"] = await _read_certificate(request, caller, claim_id, content, proof.media_type)
    return shown


async def _read_certificate(request: Request, caller: Caller, claim_id: uuid.UUID, content: bytes, media_type: str) -> dict[str, Any]:
    """The platform reads the certificate itself (its own OCR) and compares the name and number with the claim. The result is evidence for the applicant and the reviewer, never a verification:
    a certificate the applicant supplies can be forged. Nothing read is kept or returned except codes and the incorporation date. A file the reader cannot take is answered, not refused: the upload stands."""
    if media_type not in {"image/jpeg", "image/png"}:
        return {"verdict": certificate_read.NOT_ENOUGH, "reading": "PDF_NOT_READABLE_UPLOAD_A_PHOTO_OR_SCAN"}  # the reader takes JPEG and PNG only
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        name, number = await proof_service.claimed_company(conn, caller.ctx.tenant_id or "", str(claim_id))
    try:
        text = await read_page_text(content, transport=getattr(request.app.state, "imaging_transport", None))
    except ImagingUnavailable:
        return {"verdict": certificate_read.NOT_ENOUGH, "reading": "READING_UNAVAILABLE"}
    return certificate_read.compare(text, claimed_name=name, claimed_number=number)


@router.post(
    "/org-claims/{claim_id}/certificate:check",
    dependencies=[Depends(matrix_gate("b.upload-organization-kyb-evidence", Mode.EXECUTE))],
)
async def check_certificate(claim_id: uuid.UUID, request: Request, caller: CallerDep) -> dict[str, Any]:
    """Read a certificate picture and compare it with the claim, WITHOUT saving it. The file is scanned for malware, read in memory for this one answer and dropped: nothing is stored, so it needs no
    place in the company's data region. It is the same check an upload gets (`read`), offered before the person decides to add the document. Evidence only: it never verifies the company."""
    policy = proof_policy()
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():  # never read more than the limit allows, whatever the headers said
        size += len(chunk)
        if size > policy.max_bytes:
            raise _error(ProofError("PROOF_TOO_LARGE", f"The file is larger than {policy.max_bytes // (1024 * 1024)} MB."))
        chunks.append(chunk)
    content = b"".join(chunks)
    if not content:
        raise _error(ProofError("PROOF_EMPTY", "The file is empty."))
    if content.startswith(b"\xff\xd8\xff"):
        media = "image/jpeg"
    elif content.startswith(b"\x89PNG\r\n\x1a\n"):
        media = "image/png"
    else:
        raise _error(ProofError("PROOF_TYPE_NOT_ALLOWED", "Use a JPEG or PNG photograph or scan to have it checked."))
    status = await _scanner(request).scan(content)
    if status is ScanStatus.INFECTED:
        raise _error(ProofError("PROOF_INFECTED", "That file could not be accepted."))
    if status is ScanStatus.UNAVAILABLE:
        raise _error(ProofError("PROOF_SCAN_UNAVAILABLE", "The file could not be checked for safety right now. Try again soon."))
    missing: KybError | None = None
    try:
        out = await _read_certificate(request, caller, claim_id, content, media)
    except KybError as e:
        missing = e
    if missing is not None:
        await _unreachable(request, caller, claim_id, "certificate_check", missing)
    return {"stored": False, "read": out}


@router.get(
    "/org-claims/{claim_id}/proofs",
    dependencies=[Depends(matrix_gate("b.view-verification-state-expiration", Mode.READ))],
)
async def list_proofs(claim_id: uuid.UUID, request: Request, caller: CallerDep) -> dict[str, Any]:
    missing: KybError | None = None
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await _require_admin(conn, caller)
        try:
            proofs = await proof_service.list_proofs(
                conn, caller.ctx.tenant_id or "", str(claim_id)
            )
        except KybError as e:
            missing = e
    if missing is not None:
        await _unreachable(request, caller, claim_id, "list", missing)
    return {
        "claim_id": str(claim_id),
        "proofs": [_shown(p) for p in proofs],
        "limit": proof_policy().per_case_limit,
    }


@router.get("/reviews/kyb-cases/{case_id}/proofs")
async def reviewer_list_proofs(
    case_id: uuid.UUID, request: Request, caller: CallerDep
) -> dict[str, Any]:
    missing: KybError | None = None
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn, caller, action="organization.kyb.review", resource=f"kyb_case:{case_id}"
        )
        try:
            proofs = await proof_service.list_proofs(conn, caller.ctx.tenant_id or "", str(case_id))
        except KybError as e:
            missing = e
    if missing is not None:
        await _unreachable(request, caller, case_id, "review_list", missing)
    return {
        "claim_id": str(case_id),
        "proofs": [_shown(p) for p in proofs],
        "limit": proof_policy().per_case_limit,
    }


@router.get("/reviews/kyb-cases/{case_id}/proofs/{proof_id}")
async def read_proof(
    case_id: uuid.UUID, proof_id: uuid.UUID, request: Request, caller: CallerDep
) -> Response:
    missing: KybError | None = None
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn, caller, action="organization.kyb.review", resource=f"kyb_case:{case_id}"
        )
        try:
            proof, content = await proof_service.read_proof(
                conn,
                caller.ctx.tenant_id or "",
                str(case_id),
                str(proof_id),
                reader=_me(caller),
                now=datetime.now(UTC),
            )
        except ProofError as e:
            raise _error(e) from e
        except KybError as e:
            missing = e
    if missing is not None:
        await _unreachable(request, caller, case_id, "review_read", missing)
    return Response(
        content,
        media_type=proof.media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{proof.label}"',
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
            "Content-Security-Policy": "default-src 'none'; sandbox",
        },
    )
