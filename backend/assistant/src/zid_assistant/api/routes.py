"""The public API v1 routes (see backend/assistant/API.md). Anonymous, JSON only, no uploads, no identity verbs (ADR-06, INV-01).

Guards on every call: Origin allowlist and JSON content type on state-changing requests, a hard body-size cap, strict schemas that
reject unknown keys (no overposting of tenant ids, tokens or images), and per-session / per-client rate limits. Visitor text is
scanned for secrets BEFORE it reaches the pipeline, the model, a log, a metric or a store (ADR-08).
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from zid_assistant import API_PREFIX, POLICY_VERSION
from zid_assistant.api.errors import ApiError
from zid_assistant.api.state import AssistantState
from zid_assistant.domain import intake as dom_intake
from zid_assistant.domain.eligibility import link_usable
from zid_assistant.domain.sensitive import normalize, scan_chat
from zid_assistant.infrastructure.flags import Flags
from zid_assistant.infrastructure.revocations import apply
from zid_assistant.infrastructure.sessions import SessionData

router = APIRouter(prefix=API_PREFIX, tags=["public-assistant"])
MAX_BODY = 8 * 1024
_LINK_ID = re.compile(r"^[A-Z0-9-]{1,60}$")
_IDEM = re.compile(r"^[A-Za-z0-9-]{8,64}$")


def _st(request: Request) -> AssistantState:
    st: AssistantState = request.app.state.assistant
    return st


# ---------------------------------------------------------------------------------------------- guards
def _client_key(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for", "")
    return xff.split(",")[0].strip() or (request.client.host if request.client else "unknown")


def _check_origin(request: Request, st: AssistantState) -> None:
    origin = request.headers.get("origin")
    if origin is None or origin not in st.settings.allowed_origins:
        raise ApiError("ORIGIN_DENIED")


async def _json_body(request: Request) -> dict[str, Any]:
    ctype = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if ctype != "application/json":
        raise ApiError("UNSUPPORTED_MEDIA_TYPE")
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_BODY:
        raise ApiError("PAYLOAD_TOO_LARGE")
    raw = await request.body()
    if len(raw) > MAX_BODY:
        raise ApiError("PAYLOAD_TOO_LARGE")
    try:
        data = json.loads(raw or b"{}")
    except ValueError as e:
        raise ApiError("INVALID_REQUEST") from e
    if not isinstance(data, dict):
        raise ApiError("INVALID_REQUEST")
    return data


def _parse[T: BaseModel](model: type[T], data: dict[str, Any]) -> T:
    try:
        return model.model_validate(data)
    except ValidationError as e:
        loc = e.errors()[0].get("loc", ()) if e.errors() else ()
        raise ApiError("INVALID_REQUEST", field=str(loc[0]) if loc else None) from e


async def _session(request: Request, st: AssistantState) -> tuple[str, SessionData]:
    handle = request.cookies.get(st.settings.cookie_name)
    if not handle or len(handle) > 80:
        raise ApiError("INVALID_GUEST_SESSION")
    data = await st.sessions.touch(handle)
    if data is None:
        raise ApiError("INVALID_GUEST_SESSION")
    return handle, data


def _limit(st: AssistantState, bucket: str, key: str, limit: int) -> None:
    ok, retry = st.limiter.allow(bucket, key, limit)
    if not ok:
        raise ApiError("RESOURCE_LIMIT", retry_after_s=retry)


async def _flags(st: AssistantState) -> Flags:
    return await st.flags.get()


def _expires(st: AssistantState, d: SessionData) -> datetime:
    idle_end = d.last_active + st.settings.session_idle_seconds
    return datetime.fromtimestamp(min(idle_end, d.absolute_expiry), UTC).replace(microsecond=0)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------------------------- schemas
class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SessionIn(_In):
    locale: str = Field(
        default="en-US", max_length=35, pattern=r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8}){0,2}$"
    )


class MessageIn(_In):
    client_message_id: str = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9-]+$")
    text: str = Field(min_length=1)
    locale: str = Field(
        default="en-US", max_length=35, pattern=r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8}){0,2}$"
    )
    intent_hint: str | None = Field(default=None, max_length=8)  # untrusted; never used for routing
    context: dict[str, str] | None = None  # 0-3 safe slots; scanned, never trusted


class FeedbackIn(_In):
    response_id: str = Field(pattern=r"^rsp_[0-9a-f]{32}$")
    rating: str = Field(pattern=r"^(USEFUL|NOT_USEFUL)$")


class HandoffIn(_In):
    purpose: str = Field(pattern=r"^(SALES|SUPPORT)$")


# ---------------------------------------------------------------------------------------------- routes
@router.get("/bootstrap")
async def bootstrap(request: Request, response: Response) -> Response:
    st = _st(request)
    flags = await _flags(st)
    k = apply(
        st.knowledge, await st.revocations.get()
    )  # a revoked link is not offered as static help either

    def offered(ids: tuple[str, ...]) -> list[dict[str, str]]:
        """Links from the approved destination registry that are usable now and are not an authenticated flow."""
        return [
            {"link_id": lid, "label": k.snapshot.destinations[lid].label}
            for lid in ids
            if lid in k.snapshot.destinations
            and link_usable(k.snapshot, lid).allowed
            and not k.snapshot.destinations[lid].secure_route
        ]

    static = offered(
        ("OVERVIEW", "DEVELOPER-DOCS", "TALK-TO-SALES", "HELP-SUPPORT", "TRUST-CENTER", "PRICING")
    )
    footer = offered(("TALK-TO-EXPERT", "PRIVACY-NOTICE"))
    body = {
        "enabled": flags.chat_on,
        "display_name": "Zoiko iD Assistant",
        "locale": k.manifest.locale_default,
        "revision": POLICY_VERSION,
        "snapshot_id": k.snapshot.snapshot_id,
        "sample_content": k.snapshot.sample,
        "flags": {
            "ai_answers": flags.ai_on,
            "intake_sales": st.intake.available("SALES", flags),
            "intake_support": st.intake.available("SUPPORT", flags),
            "marketing": flags.marketing_on,
        },
        "limits": {
            "message_max_chars": st.settings.message_max_chars,
            "session_idle_seconds": st.settings.session_idle_seconds,
        },
        "starters": [s.model_dump() for s in k.starters] if flags.chat_on else [],
        "static_help": static,
        "footer_links": footer,
        "notice": k.copy_text(
            "privacy_notice",
            "Do not enter passwords, one-time codes, identity documents or personal records. This assistant is public and cannot access accounts.",
        ),
    }
    payload = json.dumps(body, sort_keys=True, separators=(",", ":"))
    etag = '"' + hashlib.sha256(payload.encode()).hexdigest()[:20] + '"'
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag})
    return JSONResponse(body, headers={"ETag": etag, "Cache-Control": "no-cache"})


@router.post("/sessions", status_code=201)
async def create_session(request: Request) -> Response:
    st = _st(request)
    _check_origin(request, st)
    data = _parse(
        SessionIn,
        await _json_body(request)
        if request.headers.get("content-length", "0") not in ("", "0")
        else {},
    )
    flags = await _flags(st)
    if not flags.chat_on:
        raise ApiError("UNAVAILABLE")
    _limit(st, "sessions", _client_key(request), st.settings.rate_limit_sessions)
    handle, sd = await st.sessions.create(data.locale)
    resp = JSONResponse(
        {
            "session_expires_at": _iso(_expires(st, sd)),
            "idle_seconds": st.settings.session_idle_seconds,
        },
        status_code=201,
    )
    resp.set_cookie(
        st.settings.cookie_name,
        handle,
        max_age=st.settings.session_absolute_seconds,
        httponly=True,
        samesite="lax",
        secure=st.settings.is_production_like,
        path=API_PREFIX,
    )
    resp.headers["Cache-Control"] = "no-store"
    return resp


@router.delete("/sessions", status_code=204)
async def delete_session(request: Request) -> Response:
    st = _st(request)
    _check_origin(request, st)
    handle = request.cookies.get(st.settings.cookie_name)
    if handle and len(handle) <= 80:
        await st.sessions.delete(handle)
    resp = Response(status_code=204)
    resp.delete_cookie(st.settings.cookie_name, path=API_PREFIX)
    resp.headers["Cache-Control"] = "no-store"
    return resp


@router.post("/messages")
async def post_message(request: Request) -> Response:
    st = _st(request)
    _check_origin(request, st)
    flags = await _flags(st)
    if not flags.chat_on:
        raise ApiError("UNAVAILABLE")
    handle, sd = await _session(request, st)
    body = _parse(MessageIn, await _json_body(request))
    _limit(st, "messages", handle, st.settings.rate_limit_messages)
    text = normalize(body.text).strip()
    if not text or len(text) > st.settings.message_max_chars:
        raise ApiError("INVALID_REQUEST", field="text")
    if body.context is not None and (
        len(body.context) > 3 or any(len(k) > 20 or len(v) > 40 for k, v in body.context.items())
    ):
        raise ApiError("INVALID_REQUEST", field="context")

    # Secrets / identity data are intercepted in request memory before anything else touches the text (INV-03).
    ctx_values = list(body.context.values()) if body.context else []
    finding = scan_chat(text, body.intent_hint, *ctx_values)
    if finding.blocked:
        await st.store.record_safety(
            ",".join(sorted(finding.categories))[:40], "messages"
        )  # categories only, never text
        raise ApiError("SENSITIVE_CONTENT_BLOCKED")

    digest = hashlib.sha256(f"{body.locale}\0{text}".encode()).hexdigest()
    cached = await st.sessions.get_message(handle, body.client_message_id)
    if cached is not None:
        if cached[0] != digest:
            raise ApiError("IDEMPOTENCY_CONFLICT")
        return JSONResponse(json.loads(cached[1]), headers={"Cache-Control": "no-store"})

    try:
        answered = await st.pipeline.with_revocations(
            await st.revocations.get()
        ).answer(
            text,
            st.knowledge.manifest.locale_default,  # only the approved locale is served; others get the default (OD-022)
            _expires(st, sd),
            handoff_available={
                "sales": st.intake.available("SALES", flags),
                "support": st.intake.available("SUPPORT", flags),
            },
            ai_on=flags.ai_on,
        )
    except Exception as e:
        raise ApiError("UNAVAILABLE") from e
    packet = answered.packet
    payload = packet.model_dump(mode="json")
    await st.sessions.put_message(handle, body.client_message_id, digest, json.dumps(payload))
    await st.sessions.add_response(handle, packet.response_id)
    o = answered.outcome
    await st.store.record_metric(
        o.intent_id,
        o.decision.value,
        o.reason.value,
        o.route,
        o.ai_used,
        int(o.seconds * 1000),
        packet.snapshot_id,
    )
    return JSONResponse(payload, headers={"Cache-Control": "no-store"})


@router.post("/feedback", status_code=204)
async def post_feedback(request: Request) -> Response:
    st = _st(request)
    _check_origin(request, st)
    handle, _sd = await _session(request, st)
    body = _parse(FeedbackIn, await _json_body(request))
    _limit(st, "feedback", handle, st.settings.rate_limit_messages)
    if not await st.sessions.has_response(handle, body.response_id):
        raise ApiError(
            "INVALID_REQUEST", field="response_id"
        )  # feedback is accepted only for a response this session received
    if await st.sessions.mark_feedback(handle, body.response_id):
        await st.store.record_feedback(
            body.response_id, body.rating, None, st.knowledge.snapshot.snapshot_id
        )
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


@router.get("/out/{link_id}")
async def out(link_id: str, request: Request) -> Response:
    st = _st(request)
    snap = apply(
        st.knowledge, await st.revocations.get()
    ).snapshot  # checked at click time (ADR-11)
    if not _LINK_ID.match(link_id) or not link_usable(snap, link_id).allowed:
        raise ApiError("NOT_FOUND")
    dest = snap.destinations[link_id]
    if dest.secure_route:
        raise ApiError("NOT_FOUND")  # authenticated flows are never opened from chat (ADR-01)
    return Response(
        status_code=302,
        headers={
            "Location": dest.url,
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
        },
    )


# ---------------------------------------------------------------------------------------------- intake (INT-010)
@router.get("/intake/config")
async def intake_config(request: Request) -> Response:
    st = _st(request)
    flags = await _flags(st)
    return JSONResponse(st.intake.config(flags), headers={"Cache-Control": "no-cache"})


@router.post("/handoffs")
async def handoff(request: Request) -> Response:
    st = _st(request)
    _check_origin(request, st)
    flags = await _flags(st)
    handle, _sd = await _session(request, st)
    body = _parse(HandoffIn, await _json_body(request))
    _limit(st, "handoffs", handle, st.settings.rate_limit_handoffs)
    if not st.intake.available(body.purpose, flags):
        raise ApiError("REGION_OR_POLICY_BLOCK")
    cfg = st.intake.config(flags)["sales" if body.purpose == "SALES" else "support"]
    return JSONResponse(
        cfg, headers={"Cache-Control": "no-store"}
    )  # the transcript is never attached to a handoff


async def _submit(request: Request, purpose: str) -> Response:
    st = _st(request)
    _check_origin(request, st)
    flags = await _flags(st)
    idem = request.headers.get("idempotency-key", "")
    if not _IDEM.match(idem):
        raise ApiError("INVALID_REQUEST", field="idempotency-key")
    _limit(st, "intake", _client_key(request), st.settings.rate_limit_handoffs)
    data = await _json_body(request)
    try:
        accepted = await st.intake.submit(purpose, data, idem, flags)
    except dom_intake.IntakeError as e:
        raise ApiError(e.code, field=e.field) from e
    return JSONResponse(
        {"reference": accepted.reference, "status": accepted.status},
        status_code=202,
        headers={"Cache-Control": "no-store"},
    )


@router.post("/intake/sales")
async def intake_sales(request: Request) -> Response:
    return await _submit(request, "SALES")


@router.post("/intake/support")
async def intake_support(request: Request) -> Response:
    return await _submit(request, "SUPPORT")


@router.post("/preferences/marketing", status_code=204)
async def marketing(request: Request) -> Response:
    st = _st(request)
    _check_origin(request, st)
    flags = await _flags(st)
    _limit(st, "intake", _client_key(request), st.settings.rate_limit_handoffs)
    try:
        await st.intake.record_marketing(await _json_body(request), flags)
    except dom_intake.IntakeError as e:
        raise ApiError(e.code, field=e.field) from e
    return Response(status_code=204, headers={"Cache-Control": "no-store"})
