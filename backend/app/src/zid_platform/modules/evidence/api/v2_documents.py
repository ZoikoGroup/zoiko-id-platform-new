"""POST /v2/documents/validate (ZID-INT-002 section 09): integrity, type and signature detection, each reported on its own.

The document is read from the request, checked in memory and DISCARDED: nothing about its content is stored or logged, only the
caller sees the digest. It is a small-document check (2 MB). Issuer authentication, signature and seal VALIDATION, qualified status and
long-term archive are reported UNAVAILABLE or INDETERMINATE with a reason, because no validator, trust list or issuer source exists
here; the response never offers one positive status.
"""

from __future__ import annotations

import base64
import binascii
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from zid_context import current_context
from zid_db import tenant_transaction
from zid_errors import ZidError

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.modules.evidence.domain import documents as docs
from zid_platform.protected import protected_action

router = APIRouter(tags=["evidence-v2"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]


class V2DocumentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content_base64: str = Field(min_length=4, max_length=(docs.MAX_BYTES * 4) // 3 + 16)
    declared_type: str = Field(min_length=3, max_length=40)
    expected_sha256: str | None = Field(default=None, pattern=r"^[A-Fa-f0-9]{64}$")
    checks: list[str] = Field(min_length=1, max_length=4)
    jurisdiction: dict[str, str] | None = None
    purpose: str | None = Field(default=None, max_length=64)


@router.post("/v2/documents/validate")
async def validate_document(
    body: V2DocumentIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(conn, caller, action="evidence.verify", resource="document:validate")
    try:
        content = base64.b64decode(body.content_base64, validate=True)
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
    ctx = current_context()
    return {
        "request_id": ctx.request_id if ctx else "req_unknown",
        "correlation_id": ctx.correlation_id if ctx else "cor_unknown",
        "sha256": v.sha256,
        "sniffed_type": v.sniffed_type,
        "size_bytes": v.size_bytes,
        "stored": False,
        "document_results": [
            {
                "check": r.check,
                "status": r.status.value,
                "reasons": list(r.reasons),
                "detail": r.detail,
            }
            for r in v.results
        ],
        "limitations": [
            "A hash shows unchanged bytes, not genuine issuing authority.",
            "An embedded signature is detected, not validated: no validator or trust list is available.",
            "A valid signature would still not mean lawful authority for the purpose.",
        ],
    }
