"""A licensed screening provider's callback (ZID-DTV-WF-06 section 10, ENG-15; CR-113).

    POST /v1/integrations/screening/{tenant_id}.{provider_id}/events      signed by the provider, at least once, idempotent

Trusted only if all of these hold, in this order: the provider has a signing secret on record here; the body is small and is JSON naming this provider and this tenant; the signature verifies over the exact
bytes; the clock is inside the window; the event id has not been accepted before. A refusal is recorded with its code (and can be alerted on) and never claims an event id; a replay is answered 200 and changes
nothing, so a provider that retries is not told to retry for ever and nothing is billed or decided twice. The body is never kept, only its hash. The route reads nothing of a case beyond whether it exists for
this tenant, and it decides nothing: a provider's word is evidence, not Zoiko iD's authority.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from zid_context import RequestContext
from zid_db import tenant_transaction

from zid_platform.api_auth import request_engine
from zid_platform.modules.screening.application import screening_requests as sr
from zid_platform.modules.screening.domain import candidate_detail as cd
from zid_platform.modules.screening.domain import coverage as cov
from zid_platform.modules.screening.domain import provider_controls as pc

router = APIRouter(tags=["screening-integrations"])
log = logging.getLogger("zid.screening")
MAX_BODY = 64 * 1024
_STATUS = {
    "PROVIDER_NOT_RECOGNISED": 401,
    "TENANT_MISMATCH": 401,
    "SIGNATURE_INVALID": 401,
    "TIMESTAMP_OUTSIDE_WINDOW": 400,
    "BODY_INVALID": 400,
    "BODY_TOO_LARGE": 413,
}


def _ctx(tenant: str) -> RequestContext:
    return RequestContext(
        "req_provider",
        "cor_provider",
        "screening-provider",
        "cell",
        tenant_id=tenant,
        principal_id=None,
    )


def _secrets(request: Request) -> dict[str, bytes]:
    return getattr(request.app.state, "screening_provider_secrets", None) or {}


async def _record(
    request: Request,
    tenant: str,
    provider: str,
    event_id: str,
    case_ref: str | None,
    accepted: bool,
    outcome: str,
    body: bytes,
    now: datetime,
) -> bool:
    """Store the row. False only when an accepted row already holds this (provider, event id): a replay."""
    async with tenant_transaction(request_engine(request), _ctx(tenant)) as conn:
        row = (
            await conn.execute(
                text(
                    "INSERT INTO verify.screening_provider_event (row_id, tenant_id, provider_id, event_id, case_ref, accepted, outcome, body_sha256, received_at) "
                    "VALUES (:r, :t, :p, :e, :c, :a, :o, :h, :at) ON CONFLICT DO NOTHING RETURNING row_id"
                ),
                {
                    "r": uuid.uuid4(),
                    "t": uuid.UUID(tenant),
                    "p": provider,
                    "e": event_id[:128] or "none",
                    "c": case_ref,
                    "a": accepted,
                    "o": outcome,
                    "h": hashlib.sha256(body).hexdigest(),
                    "at": now,
                },
            )
        ).first()
    return row is not None


async def _seen(request: Request, tenant: str, provider: str, event_id: str) -> set[str]:
    async with tenant_transaction(request_engine(request), _ctx(tenant)) as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT 1 FROM verify.screening_provider_event WHERE provider_id = :p AND event_id = :e AND accepted"
                ),
                {"p": provider, "e": event_id[:128]},
            )
        ).first()
    return {event_id} if row else set()


@router.post("/v1/integrations/screening/{ref}/events", status_code=202)
async def event(ref: str, request: Request) -> JSONResponse:
    tenant, _, provider = ref.partition(".")
    now = datetime.now(UTC)
    try:
        uuid.UUID(tenant)
    except ValueError:
        return JSONResponse(
            {"error": "PROVIDER_NOT_RECOGNISED"}, status_code=401
        )  # not a route we serve: nothing is recorded
    if not provider or len(provider) > 64:
        return JSONResponse(
            {"error": "PROVIDER_NOT_RECOGNISED"}, status_code=401
        )  # no provider named, or a name no provider has: not a route we serve, and the table cannot hold it, so nothing is recorded
    body = await request.body()
    event_id = request.headers.get("x-zoiko-event-id", "")

    async def refuse(code: str, case_ref: str | None = None) -> JSONResponse:
        await _record(request, tenant, provider, event_id, case_ref, False, code, body, now)
        log.warning("screening provider callback refused: %s (provider %s)", code, provider)
        return JSONResponse({"error": code}, status_code=_STATUS.get(code, 400))

    if len(body) > MAX_BODY:
        return await refuse("BODY_TOO_LARGE")
    secret = _secrets(request).get(provider)
    if secret is None or not provider:
        return await refuse("PROVIDER_NOT_RECOGNISED")
    try:
        doc: dict[str, Any] = json.loads(body)
        issued = datetime.fromisoformat(request.headers.get("x-zoiko-timestamp", ""))
        if issued.tzinfo is None:
            raise ValueError("naive timestamp")
        named_provider, named_tenant = str(doc["provider_id"]), str(doc["tenant_id"])
        case_ref = str(doc["case_ref"]) if doc.get("case_ref") else None
    except (ValueError, KeyError, TypeError):
        return await refuse("BODY_INVALID")
    candidate = pc.Event(named_provider, named_tenant, event_id, case_ref or "", issued, body)
    try:
        pc.verify_event(
            candidate,
            request.headers.get("x-zoiko-signature", ""),
            secret=secret,
            expected_provider=provider,
            expected_tenant=tenant,
            seen=await _seen(request, tenant, provider, event_id),
            now=now,
        )
    except pc.EventError as e:
        if e.code == "DUPLICATE_EVENT":
            return JSONResponse(
                {"outcome": "DUPLICATE"}, status_code=200
            )  # a replay: recognised, answered, and nothing changes
        return await refuse(e.code, case_ref)
    if not await _record(
        request, tenant, provider, event_id, case_ref, True, "ACCEPTED", body, now
    ):
        return JSONResponse(
            {"outcome": "DUPLICATE"}, status_code=200
        )  # two at once: the index let one in
    reopened = await _apply_update(request, tenant, named_provider, doc, event_id, now)
    return JSONResponse({"outcome": "ACCEPTED", "cases_reopened": reopened}, status_code=202)


async def _apply_update(
    request: Request, tenant: str, provider: str, doc: dict[str, Any], event_id: str, now: datetime
) -> int:
    """A provider's retraction, correction or removal of something a case relied on sends that case back to a person with a stated reason (WF06 QA-026). A retraction never clears the subject and a
    correction never escalates one: the case is reopened and a person decides. An event of another kind, or one naming no references or an unknown class, changes nothing."""
    reason = cd.update_reason(str(doc.get("kind", "")))
    refs = doc.get("refs")
    klass = str(doc.get("class", ""))
    if (
        reason is None
        or not isinstance(refs, list)
        or not 0 < len(refs) <= 200
        or not all(isinstance(r, str) and 0 < len(r) <= 200 for r in refs)
        or klass not in {k.value for k in cov.Klass}
    ):
        return 0
    async with tenant_transaction(request_engine(request), _ctx(tenant)) as conn:
        return await sr.reopen_for_delta(
            conn,
            f"{provider}:{klass}",
            refs,
            release_id=event_id[:128] or "none",
            now=now,
            code=reason,
        )
