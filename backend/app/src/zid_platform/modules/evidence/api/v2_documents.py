"""POST /v2/documents/validate and GET /v2/documents/{id} (ZID-INT-002 section 09): integrity, type and signature detection, each on its own.

Two ways in, exactly one per request:
- `content_base64`: a small document (2 MB) is checked in memory and DISCARDED. Nothing about it is stored or logged.
- `evidence_ref`: an upload that is READY and scanned CLEAN. Its result is kept (statuses, reason codes, details, the digest: never the
  document) so `GET /v2/documents/{validation_id}` can return it later. A stored result belongs to its upload and goes with it.

Issuer authentication, signature and seal VALIDATION, qualified status and long-term archive are reported UNAVAILABLE or INDETERMINATE with
a reason, because no validator, trust list or issuer source exists here; the response never offers one positive status.
"""

from __future__ import annotations

import base64
import binascii
import json
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from zid_context import current_context
from zid_db import tenant_transaction
from zid_errors import ZidError

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.modules.evidence.application import signatures, uploads
from zid_platform.modules.evidence.domain import documents as docs
from zid_platform.modules.evidence.infrastructure.trust_config import trust_config
from zid_platform.protected import protected_action

MAX_SIGNATURE_B64 = 131072  # a CAdES signature with its chain is a few kilobytes
router = APIRouter(tags=["evidence-v2"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]
LIMITATIONS = [
    "A hash shows unchanged bytes, not genuine issuing authority.",
    "A signature is verified cryptographically and against the trust anchors this deployment configured; revocation is not checked and the signing time is the signer's own claim.",
    "Qualified status is reported only from an authenticated trusted list; without one it is NOT_ASSESSED.",
    "A valid signature would still not mean lawful authority for the purpose.",
]


class V2DocumentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content_base64: str | None = Field(
        default=None, min_length=4, max_length=(docs.MAX_BYTES * 4) // 3 + 16
    )
    evidence_ref: str | None = Field(
        default=None, max_length=80
    )  # an upload that is READY and scanned CLEAN
    declared_type: str = Field(min_length=3, max_length=40)
    signature_base64: str | None = Field(
        default=None, min_length=4, max_length=MAX_SIGNATURE_B64
    )  # a detached CAdES signature (.p7s) over the content
    expected_sha256: str | None = Field(default=None, pattern=r"^[A-Fa-f0-9]{64}$")
    checks: list[str] = Field(min_length=1, max_length=4)
    jurisdiction: dict[str, str] | None = None
    purpose: str | None = Field(default=None, max_length=64)


def _ids() -> dict[str, str]:
    ctx = current_context()
    return {
        "request_id": ctx.request_id if ctx else "req_unknown",
        "correlation_id": ctx.correlation_id if ctx else "cor_unknown",
    }


def _results(v: docs.Validation) -> list[dict[str, Any]]:
    return [
        {"check": r.check, "status": r.status.value, "reasons": list(r.reasons), "detail": r.detail}
        for r in v.results
    ]


@router.post("/v2/documents/validate")
async def validate_document(
    body: V2DocumentIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    if (body.content_base64 is None) == (body.evidence_ref is None):
        raise ZidError(
            "VALIDATION_FAILED",
            "Send exactly one of content_base64 and evidence_ref.",
            http_status=422,
        )
    content: bytes
    upload_id: str | None = None
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(conn, caller, action="evidence.verify", resource="document:validate")
        held = None
        if body.evidence_ref is not None:
            held = await uploads.read_usable(conn, body.evidence_ref, datetime.now(UTC))
            if (
                held is None
            ):  # unknown, not scanned clean, deleted or past retention: all the same answer
                raise ZidError(
                    "EVIDENCE_NOT_USABLE", "That evidence cannot be used.", http_status=422
                )
            upload_id = str(held[1]["upload_id"])
    if held is not None:
        content = held[0]
    else:
        try:
            content = base64.b64decode(body.content_base64 or "", validate=True)
        except (binascii.Error, ValueError) as e:
            raise ZidError(
                "VALIDATION_FAILED", "content_base64 is not valid base64.", http_status=422
            ) from e
    try:
        v = docs.validate(
            content,
            declared_type=body.declared_type,
            expected_sha256=body.expected_sha256,
            checks=body.checks,
        )
    except docs.DocumentError as e:
        status = 413 if e.code == "DOCUMENT_TOO_LARGE" else 422
        raise ZidError(
            e.code, e.code.replace("_", " ").capitalize() + ".", http_status=status
        ) from e
    if (
        {"signature", "seal"} & set(body.checks)
    ):  # a signed PDF or XML, or a detached CAdES signature, is checked for real; anything else keeps the detection-only answer
        config = getattr(request.app.state, "trust_config", None) or trust_config()
        if body.signature_base64 is not None:
            try:
                detached = base64.b64decode(body.signature_base64, validate=True)
            except (binascii.Error, ValueError) as e:
                raise ZidError(
                    "VALIDATION_FAILED", "signature_base64 is not valid base64.", http_status=422
                ) from e
            v = signatures.apply_detached(v, content, detached, config)
        else:
            v = signatures.apply(v, content, config)
    validation_id: str | None = None
    if (
        upload_id is not None
    ):  # only a result about an upload is kept; pasted content leaves no trace
        validation_id = str(uuid.uuid4())
        async with tenant_transaction(request_engine(request), caller.ctx) as conn:
            await conn.execute(
                text(
                    "INSERT INTO evidence.document_validation (validation_id, tenant_id, upload_id, requested_by, checks, sha256, results) "
                    "VALUES (:v, :t, :u, :r, :c, :h, CAST(:j AS jsonb))"
                ),
                {
                    "v": uuid.UUID(validation_id),
                    "t": uuid.UUID(caller.ctx.tenant_id or ""),
                    "u": uuid.UUID(upload_id),
                    "r": uuid.UUID(str(caller.ctx.principal_id)),
                    "c": [r.check for r in v.results],
                    "h": v.sha256,
                    "j": json.dumps(_results(v)),
                },
            )
    return {
        **_ids(),
        "validation_id": f"docval_{validation_id}" if validation_id else None,
        "sha256": v.sha256,
        "sniffed_type": v.sniffed_type,
        "size_bytes": v.size_bytes,
        "stored": validation_id is not None,  # the RESULT, never the pasted document
        "document_results": _results(v),
        "limitations": LIMITATIONS,
    }


@router.get("/v2/documents/{validation_ref}")
async def read_validation(
    validation_ref: str, request: Request, caller: CallerDep
) -> dict[str, Any]:
    try:
        vid = uuid.UUID(validation_ref.removeprefix("docval_"))
    except ValueError as e:
        raise ZidError("VALIDATION_FAILED", "A reference is not valid.", http_status=422) from e
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(conn, caller, action="evidence.verify", resource=f"document:{vid}")
        row = (
            (
                await conn.execute(
                    text(
                        "SELECT v.validation_id, v.upload_id, v.sha256, v.results, v.created_at, u.state AS upload_state "
                        "FROM evidence.document_validation v JOIN evidence.upload u USING (upload_id) WHERE v.validation_id = :v"
                    ),
                    {"v": vid},
                )
            )
            .mappings()
            .first()
        )
    if row is None:
        raise ZidError("NOT_FOUND", "Not found.", http_status=404)
    return {
        **_ids(),
        "validation_id": f"docval_{row['validation_id']}",
        "evidence_ref": f"ev_{row['upload_id']}",
        "upload_state": row[
            "upload_state"
        ],  # the bytes may be gone; the result stays with the upload's record
        "sha256": row["sha256"],
        "checked_at": row["created_at"].isoformat(),
        "document_results": row["results"],
        "limitations": LIMITATIONS,
    }
