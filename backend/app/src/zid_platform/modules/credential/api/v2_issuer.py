"""The /v2 issuer-check facade (ZID-INT-002 sections 03 and 15).

    POST /v2/issuer-checks          ask a registered issuer directly (Idempotency-Key required)
    GET  /v2/issuer-checks/{ref}    the state of that question

It adapts the /v1 source-outreach routes and keeps their rules: the issuer must be in the registry, the contact addresses are the ones
registered out of band (never taken from the request), an answer needs an authenticated, signed response and two reviewers other than
the requester, and silence is UNVERIFIABLE, never "false".

The nonce the issuer must echo is shown ONCE, in the answer to the first request. The stored idempotency record never holds it, so a
replay says it was already shown instead of returning it again.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, ConfigDict, Field
from zid_context import current_context
from zid_db import tenant_transaction
from zid_errors import ZidError
from zid_idempotency import Scope, begin, complete

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.modules.credential.api import trust as t1
from zid_platform.modules.credential.application import trust_service as svc
from zid_platform.modules.credential.application import v2_view as vw
from zid_platform.modules.credential.domain import outreach as oc
from zid_platform.modules.credential.infrastructure import trust_store
from zid_platform.modules.credential.infrastructure.registries import issuer_registry

router = APIRouter(tags=["credential-v2"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]
IdemKey = Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)]


def _ids() -> dict[str, str]:
    ctx = current_context()
    return {
        "request_id": ctx.request_id if ctx else "req_unknown",
        "correlation_id": ctx.correlation_id if ctx else "cor_unknown",
    }


class V2IssuerCheckIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    issuer_ref: str = Field(min_length=1, max_length=100)
    credential_type: str = Field(pattern=r"^[a-z_]{3,32}$")
    days: int = Field(default=14, ge=1, le=60)


@router.post("/v2/issuer-checks", status_code=201)
async def open_issuer_check(
    body: V2IssuerCheckIn, request: Request, caller: CallerDep, idempotency_key: IdemKey
) -> dict[str, Any]:
    claim_type = vw.CREDENTIAL_TYPES.get(body.credential_type)
    if claim_type is None:
        raise ZidError("VALIDATION_FAILED", "credential_type is not supported.", http_status=422)
    issuer = issuer_registry().issuers.get(body.issuer_ref)
    if issuer is None:  # a lookalike or unknown issuer is never contacted
        raise ZidError("ISSUER_NOT_FOUND", "Issuer not found.", http_status=404)
    scope = Scope(caller.ctx.tenant_id or "", caller.client_id, "POST /v2/issuer-checks")
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        stored = await begin(
            conn, scope=scope, key=idempotency_key, payload=body.model_dump(mode="json")
        )
        if stored is not None:
            return {**dict(stored.body), **_ids(), "nonce_shown_once": True}
        await t1._gate(
            conn, caller, "credential.outreach.manage", f"outreach:{body.issuer_ref}"
        )
        try:
            req, nonce = await svc.open_outreach(
                conn,
                tenant_id=caller.ctx.tenant_id or "",
                issuer_id=body.issuer_ref,
                claim_type=claim_type.value,
                requested_by=t1._subject(caller, None),
                contacts=issuer.outreach_contacts,  # registered out of band, never from the request
                days=body.days,
                now=t1._now(),
                subject=None,
                claim=None,
            )
        except oc.OutreachError as e:
            raise t1._err(e) from e
        view = {
            "issuer_check_ref": f"ichk_{req.request_id}",
            "state": req.state.value,
            "deadline": req.deadline.isoformat(),
        }
        # what is stored for replay never holds the nonce
        await complete(conn, scope=scope, key=idempotency_key, status=201, body=view)
    return {**view, **_ids(), "nonce": nonce}


@router.get("/v2/issuer-checks/{issuer_check_ref}")
async def read_issuer_check(
    issuer_check_ref: str, request: Request, caller: CallerDep
) -> dict[str, Any]:
    raw = issuer_check_ref.removeprefix("ichk_")
    try:
        rid = str(uuid.UUID(raw))
    except ValueError as e:
        raise ZidError("VALIDATION_FAILED", "A reference is not valid.", http_status=422) from e
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await t1._gate(conn, caller, "credential.outreach.manage", f"outreach:{rid}")
        row = await trust_store.load_outreach(conn, rid)
    if row is None:
        raise ZidError("NOT_FOUND", "Not found.", http_status=404)
    return {
        **_ids(),
        "issuer_check_ref": f"ichk_{rid}",
        "state": row["state"],
        "outcome": row["outcome"],
        "deadline": row["deadline"].isoformat(),
        # an unanswered question is UNVERIFIABLE at the deadline, never "false" and never fraud
        "evidence_manifest_ref": f"manifest_{row['check_id']}" if row["check_id"] else None,
    }
