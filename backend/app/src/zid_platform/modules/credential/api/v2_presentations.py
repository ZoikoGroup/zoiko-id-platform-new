"""The /v2 presentation-request facade (ZID-INT-002 section 12): POST /v2/presentations/requests, GET /v2/presentations/{id}.

It adapts the /v1 OID4VP verifier and keeps all of its rules: an active credential configuration, a relying party registered for the
purpose, processing checks, claims minimized to what the registration allows, and an authorization request a wallet fetches once.

Two things the document's request does not carry, and how they are handled WITHOUT inventing policy:
- `configuration_id`: which credential configuration is being asked for. It is an explicit, required extra field of this facade; the
  platform never picks a configuration from the claim names.
- `expires_in_seconds`: honoured only as a SHORTER life (1 to 600). A longer request is refused, because a longer-lived request is
  more exposure, not less (section 12: minimize and avoid tracking).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, ConfigDict, Field
from zid_context import current_context
from zid_db import tenant_transaction
from zid_errors import ZidError
from zid_idempotency import Scope, begin, complete

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.modules.credential.api import credentials as v1
from zid_platform.modules.credential.application import verifier
from zid_platform.modules.credential.domain.credentials import CredentialError

router = APIRouter(tags=["credential-v2"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]
IdemKey = Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)]
MAX_SECONDS = int(verifier.REQUEST_TTL.total_seconds())


def _ids() -> dict[str, str]:
    ctx = current_context()
    return {
        "request_id": ctx.request_id if ctx else "req_unknown",
        "correlation_id": ctx.correlation_id if ctx else "cor_unknown",
    }


class V2PresentationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    configuration_id: uuid.UUID  # extension: which configuration is asked for (never inferred)
    recipient_ref: str = Field(min_length=1, max_length=200)
    purpose: str = Field(min_length=1, max_length=128)
    requested_claims: list[str] = Field(min_length=1, max_length=30)
    jurisdiction: dict[str, str]
    expires_in_seconds: int = Field(default=MAX_SECONDS, ge=1)
    consent_ref: str | None = Field(default=None, max_length=200)
    subject_ref: str | None = Field(default=None, max_length=80)
    response_mode: str = Field(default="direct_post", pattern=r"^direct_post(\.jwt)?$")


@router.post("/v2/presentations/requests", status_code=201)
async def request_presentation(
    body: V2PresentationIn, request: Request, caller: CallerDep, idempotency_key: IdemKey
) -> dict[str, Any]:
    v1._scope(caller, "presentation:request")
    if body.expires_in_seconds > MAX_SECONDS:
        raise ZidError(
            "VALIDATION_FAILED",
            f"expires_in_seconds can only shorten the request (at most {MAX_SECONDS}).",
            http_status=422,
        )
    country = body.jurisdiction.get("country", "")
    if len(country) != 2 or not country.isalpha():
        raise ZidError("VALIDATION_FAILED", "jurisdiction.country is required.", http_status=422)
    subject = None
    if body.subject_ref:
        raw = body.subject_ref.removeprefix("sub_")
        try:
            subject = str(uuid.UUID(raw))
        except ValueError as e:
            raise ZidError("VALIDATION_FAILED", "A reference is not valid.", http_status=422) from e
    scope = Scope(caller.ctx.tenant_id or "", caller.client_id, "POST /v2/presentations/requests")
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        stored = await begin(
            conn, scope=scope, key=idempotency_key, payload=body.model_dump(mode="json")
        )
        if stored is not None:
            return {**dict(stored.body), **_ids()}
        try:
            made = await verifier.create(
                conn,
                v1._verifier(request),
                tenant_id=caller.ctx.tenant_id or "",
                client_id=caller.client_id,
                configuration_id=str(body.configuration_id),
                purpose=body.purpose,
                claims=body.requested_claims,
                subject_principal_id=subject,
                jurisdiction=country.upper(),
                now=datetime.now(UTC),
                response_mode=body.response_mode,
                ttl=timedelta(seconds=body.expires_in_seconds),
            )
        except CredentialError as e:
            raise v1._err(e) from e
        out = {
            "presentation_ref": f"pres_{made['presentation_id']}",
            "recipient_ref": body.recipient_ref,
            "consent_ref": body.consent_ref,
            "claims": made["claims"],  # the minimized set, which may be fewer than asked for
            "request_uri": made["request_uri"],
            "authorization_request": made["authorization_request"],
            "expires_at": made["expires_at"],
        }
        await complete(conn, scope=scope, key=idempotency_key, status=201, body=out)
    return {**out, **_ids()}


@router.get("/v2/presentations/{presentation_ref}")
async def read_presentation(
    presentation_ref: str, request: Request, caller: CallerDep
) -> dict[str, Any]:
    raw = presentation_ref.removeprefix("pres_")
    try:
        pid = uuid.UUID(raw)
    except ValueError as e:
        raise ZidError("VALIDATION_FAILED", "A reference is not valid.", http_status=422) from e
    out = await v1.read_presentation(pid, request, caller)
    return {**_ids(), "presentation_ref": f"pres_{pid}", **out}
