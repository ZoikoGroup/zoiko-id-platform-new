"""Security signals and Shared Signals API (ZID-SIGNALS-001 §26; RFC 8935, RFC 8936).

GET  /.well-known/ssf-configuration                transmitter metadata
POST /ssf/events/push/{tenant}.{stream}            receive a pushed SET (stream credential)
POST /ssf/poll/{tenant}.{stream}                   receiver client polls queued SETs + acks
GET  /ssf/streams                                  list streams             (signals.stream.read)
POST /ssf/streams                                  create a stream          (signals.stream.manage)
GET  /ssf/streams/{id}                             read a stream            (signals.stream.manage)
POST /ssf/streams/{id}:enable | :disable           enable / quarantine      (signals.stream.manage)
POST /ssf/streams/{id}/subjects                    map an opaque subject    (signals.stream.manage)
POST /signals                                      trusted internal source  (scope signals:publish)
GET  /security-state/{subject_type}/{id}           current state (self, or scope security-state:read)
POST /security-state/{subject_type}/{id}:reevaluate governed override       (security_state.override)
"""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from zid_context import RequestContext, current_context
from zid_db import tenant_transaction
from zid_errors import ZidError

from zid_platform.api_auth import Caller, authenticated_caller, http_error, request_engine
from zid_platform.modules.signals.application import state as state_service
from zid_platform.modules.signals.application.ingest import CanonicalSignal, ingest
from zid_platform.modules.signals.application.ssf import (
    SsfError,
    parse_stream_ref,
    receive_push,
    ssf_configuration,
)
from zid_platform.modules.signals.domain.rules import RULES, SignalError, State
from zid_platform.modules.signals.infrastructure import signal_store as store
from zid_platform.protected import protected_action

router = APIRouter(tags=["signals"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]


def _ctx() -> RequestContext:
    return current_context() or RequestContext("req_unknown", "cor_unknown", "zoiko-id", "unknown")


def _issuer(request: Request) -> str:
    s = getattr(request.app.state, "oauth_settings", None)
    if s is None:
        raise ZidError("DEPENDENCY_UNAVAILABLE", "No issuer configured.", http_status=503)
    return str(s.issuer)


@router.get("/.well-known/ssf-configuration")
async def configuration(request: Request) -> dict[str, Any]:
    return ssf_configuration(_issuer(request))


@router.get("/ssf/jwks")
async def set_keys(request: Request) -> dict[str, Any]:
    rings = getattr(request.app.state, "keyrings", None)
    return rings.set.jwks() if rings else {"keys": []}


@router.post("/ssf/events/push/{stream_ref}", status_code=202)
async def push(stream_ref: str, request: Request) -> Response:
    try:
        await receive_push(
            request_engine(request),
            stream_ref=stream_ref,
            authorization=request.headers.get("authorization"),
            content_type=request.headers.get("content-type"),
            body=await request.body(),
            base_ctx=_ctx(),
            now=datetime.now(UTC),
        )
    except SsfError as e:
        return JSONResponse({"err": e.err, "description": e.description}, status_code=e.status)
    except SignalError as e:
        return JSONResponse({"err": "invalid_request", "description": e.code}, status_code=400)
    return Response(status_code=202)


class PollIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    ack: list[str] = Field(default_factory=list, max_length=100)
    maxEvents: int = Field(default=10, ge=1, le=100)
    returnImmediately: bool = True


@router.post("/ssf/poll/{stream_ref}")
async def poll(
    stream_ref: str, body: PollIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    tenant, stream_id = parse_stream_ref(stream_ref)
    if tenant != caller.ctx.tenant_id:
        raise http_error("STREAM_NOT_FOUND")
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        stream = await store.load_stream(conn, stream_id)
        if (
            stream is None
            or stream.direction != "TRANSMITTER"
            or stream.receiver_client_id != caller.client_id
        ):
            raise http_error("STREAM_NOT_FOUND")
        if stream.status != "ACTIVE":
            raise ZidError("STREAM_DISABLED", "Stream is not active.", http_status=403)
        sets = await store.poll(
            conn, stream_id, ack=body.ack, max_events=body.maxEvents + 1, now=datetime.now(UTC)
        )
    return {
        "sets": dict(sets[: body.maxEvents]),
        "moreAvailable": len(sets) > body.maxEvents,
    }


Alg = Literal["ES256", "Ed25519", "RS256", "PS256"]
SubjectFormat = Literal["iss_sub", "opaque"]


def _default_algs() -> list[Alg]:
    return ["ES256"]


def _default_formats() -> list[SubjectFormat]:
    return ["iss_sub", "opaque"]


class StreamIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    direction: Literal["RECEIVER", "TRANSMITTER"]
    issuer: str | None = Field(default=None, max_length=512)  # receiver: the transmitter's iss
    audience: str = Field(min_length=1, max_length=512)
    jwks: dict[str, Any] | None = None  # receiver: pinned transmitter keys
    algorithms: list[Alg] = Field(default_factory=_default_algs)
    events: list[str] = Field(min_length=1, max_length=32)
    subject_formats: list[SubjectFormat] = Field(default_factory=_default_formats)
    source_trust: Literal["INFORMATIONAL", "TRUSTED", "AUTHORITATIVE"] = "TRUSTED"
    federation_issuer: str | None = None
    receiver_client_id: str | None = None  # transmitter: the OAuth client that polls


def _stream_view(s: store.Stream) -> dict[str, Any]:
    return {
        "stream_id": s.stream_id,
        "stream_ref": f"{s.tenant_id}.{s.stream_id}",
        "direction": s.direction,
        "issuer": s.issuer,
        "audience": s.audience,
        "events": list(s.events),
        "subject_formats": list(s.subject_formats),
        "delivery_method": s.delivery_method,
        "source_trust": s.source_trust,
        "status": s.status,
        "metadata_revision": s.metadata_revision,
    }


@router.post("/ssf/streams", status_code=201)
async def create_stream(body: StreamIn, request: Request, caller: CallerDep) -> dict[str, Any]:
    tenant = caller.ctx.tenant_id or ""
    stream_id = str(uuid.uuid4())
    push_token = None
    if body.direction == "RECEIVER":
        if not body.issuer or not body.jwks or not isinstance(body.jwks.get("keys"), list):
            raise http_error("VALIDATION_FAILED", "a receiver stream pins issuer and keys")
        push_token = secrets.token_urlsafe(32)
    elif not body.receiver_client_id:
        raise http_error("VALIDATION_FAILED", "a transmitter stream names its receiver client")
    stream = store.Stream(
        stream_id=stream_id,
        tenant_id=tenant,
        direction=body.direction,
        issuer=(body.issuer or "") if body.direction == "RECEIVER" else _issuer(request),
        audience=body.audience,
        jwks=body.jwks if body.direction == "RECEIVER" else None,
        algorithms=tuple(body.algorithms),
        events=tuple(body.events),
        subject_formats=tuple(body.subject_formats),
        delivery_method="push" if body.direction == "RECEIVER" else "poll",
        push_token_hash=store.sha256(push_token) if push_token else None,
        source_trust=body.source_trust,
        federation_issuer=body.federation_issuer,
        receiver_client_id=body.receiver_client_id,
        status="PENDING",
        metadata_revision=1,
    )
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn, caller, action="signals.stream.manage", resource=f"ssf_stream:{stream_id}"
        )
        await store.create_stream(conn, stream)
    out = _stream_view(stream)
    if push_token:
        out["push_authorization"] = f"Bearer {push_token}"  # shown once; only its hash is kept
    return out


@router.get("/ssf/streams")
async def list_streams(request: Request, caller: CallerDep) -> dict[str, Any]:
    """Pinned keys and the push credential's hash never leave the store (same view as one stream)."""
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(conn, caller, action="signals.stream.read", resource="ssf_stream:*")
        return {"items": [_stream_view(s) for s in await store.list_streams(conn)]}


@router.get("/ssf/streams/{stream_id}")
async def read_stream(stream_id: uuid.UUID, request: Request, caller: CallerDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn, caller, action="signals.stream.manage", resource=f"ssf_stream:{stream_id}"
        )
        s = await store.load_stream(conn, str(stream_id))
    if s is None:
        raise http_error("STREAM_NOT_FOUND")
    return _stream_view(s)


async def _set_status(
    request: Request, caller: Caller, stream_id: str, status: str
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn,
            caller,
            action="signals.stream.manage",
            resource=f"ssf_stream:{stream_id}",
        )
        if not await store.set_stream_status(conn, stream_id, status):
            raise http_error("STREAM_NOT_FOUND")
    return {"stream_id": stream_id, "status": status}


@router.post("/ssf/streams/{stream_id}:enable")
async def enable(stream_id: uuid.UUID, request: Request, caller: CallerDep) -> dict[str, Any]:
    return await _set_status(request, caller, str(stream_id), "ACTIVE")


@router.post("/ssf/streams/{stream_id}:disable")
async def disable(stream_id: uuid.UUID, request: Request, caller: CallerDep) -> dict[str, Any]:
    return await _set_status(request, caller, str(stream_id), "QUARANTINED")


@router.get("/ssf/streams/{stream_id}/propagation")
async def propagation(stream_id: uuid.UUID, request: Request, caller: CallerDep) -> dict[str, Any]:
    """FR-SIG-003/004: commit -> receiver acknowledgement latency against the risk-class SLOs."""
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn, caller, action="signals.stream.manage", resource=f"ssf_stream:{stream_id}"
        )
        if await store.load_stream(conn, str(stream_id)) is None:
            raise http_error("STREAM_NOT_FOUND")
        stats = await store.propagation(conn, str(stream_id), datetime.now(UTC))
    return {"stream_id": str(stream_id), **stats}


class SubjectIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    opaque_id: str = Field(min_length=1, max_length=256)
    principal_id: uuid.UUID


@router.post("/ssf/streams/{stream_id}/subjects", status_code=201)
async def map_subject(
    stream_id: uuid.UUID, body: SubjectIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn, caller, action="signals.stream.manage", resource=f"ssf_stream:{stream_id}"
        )
        if await store.load_stream(conn, str(stream_id)) is None:
            raise http_error("STREAM_NOT_FOUND")
        await store.map_subject(
            conn,
            stream_id=str(stream_id),
            tenant_id=caller.ctx.tenant_id or "",
            opaque_id=body.opaque_id,
            principal_id=str(body.principal_id),
        )
    return {"stream_id": str(stream_id), "opaque_id": body.opaque_id}


class SignalIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_type: str
    subject_type: Literal["PRINCIPAL", "SESSION", "CREDENTIAL"]
    subject_id: uuid.UUID
    source_event_id: str = Field(min_length=1, max_length=256)
    effective_at: datetime | None = None


@router.post("/signals", status_code=202)
async def publish(body: SignalIn, request: Request, caller: CallerDep) -> dict[str, Any]:
    if "signals:publish" not in caller.scope:
        raise ZidError("SCOPE_INSUFFICIENT", "signals:publish is required.", http_status=403)
    if body.event_type not in RULES:
        raise http_error("VALIDATION_FAILED", "unknown canonical event type")
    now = datetime.now(UTC)
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        try:
            out = await ingest(
                conn,
                CanonicalSignal(
                    tenant_id=caller.ctx.tenant_id or "",
                    subject_type=body.subject_type,
                    subject_ref=str(body.subject_id),
                    source_id=f"client:{caller.client_id}",
                    source_event_id=body.source_event_id,
                    source_event_type=f"zoiko:{body.event_type}",
                    event_type=body.event_type,
                    effective_at=body.effective_at or now,
                    observed_at=now,
                    confidence="HIGH",
                ),
                now=now,
            )
        except SignalError as e:
            raise http_error("VALIDATION_FAILED", e.code) from e
    return {
        "signal_id": out.signal_id or None,
        "status": out.status,
        "state": out.state,
        "revision": out.revision,
    }


SubjectType = Literal["PRINCIPAL", "SESSION", "CREDENTIAL"]


@router.get("/security-state/{subject_type}/{subject_id}")
async def read_state(
    subject_type: SubjectType, subject_id: uuid.UUID, request: Request, caller: CallerDep
) -> dict[str, Any]:
    own = subject_type == "PRINCIPAL" and str(subject_id) == caller.ctx.principal_id
    if not own and "security-state:read" not in caller.scope:
        raise ZidError("SCOPE_INSUFFICIENT", "security-state:read is required.", http_status=403)
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        return await state_service.read(conn, subject_type, str(subject_id))


class OverrideIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    state: Literal["TRUSTED", "ELEVATED_RISK", "STEP_UP_REQUIRED", "REAUTH_REQUIRED", "RESTRICTED"]
    reason: str = Field(min_length=3, max_length=500)
    approval_request_id: str | None = None


@router.post("/security-state/{subject_type}/{subject_id}:reevaluate")
async def reevaluate(
    subject_type: SubjectType,
    subject_id: uuid.UUID,
    body: OverrideIn,
    request: Request,
    caller: CallerDep,
) -> dict[str, Any]:
    resource = f"security_state:{subject_type}:{subject_id}"
    now = datetime.now(UTC)
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn,
            caller,
            action="security_state.override",
            resource=resource,
            change=body.state,
            approval_request_id=body.approval_request_id,
            now=now,
        )
        try:
            revision = await state_service.override(
                conn,
                tenant_id=caller.ctx.tenant_id or "",
                subject_type=subject_type,
                subject_id=str(subject_id),
                target=State(body.state),
                by_principal=caller.ctx.principal_id or "",
                reason=body.reason,
                now=now,
            )
        except ValueError as e:
            raise http_error("SECURITY_STATE_TERMINAL") from e
    return {"subject_id": str(subject_id), "state": body.state, "revision": revision}
