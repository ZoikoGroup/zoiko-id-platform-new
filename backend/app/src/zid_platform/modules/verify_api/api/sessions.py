"""Verify API sessions, decisions and the sandbox (CR-150; ZID-EXT-IDV-001 section 10, WF-04, WF-09 to WF-14).

The partner's server (a bearer token from `/v1/verify-api/oauth2/token`, scoped):
GET  /v1/verify-api/workflows                                          the approved workflows and versions            workflows:read
GET  /v1/verify-api/products                                           the checks and where they are available        products:read
POST /v1/verify-api/verification-sessions                              create one session, safely repeatable          verifications:create
GET  /v1/verify-api/verification-sessions                              page through your sessions                      verifications:list
GET  /v1/verify-api/verification-sessions/{id}                         where one session is                            verifications:read
GET  /v1/verify-api/verification-sessions/{id}/decision                the minimized decision                          decisions:read
POST /v1/verify-api/verification-sessions/{id}:cancel                  end a session                                   verifications:create
POST /v1/verify-api/verification-sessions/{id}/resume-token            a fresh launch link for a person who left      verifications:resume
POST /v1/verify-api/verification-sessions/{id}:recheck                  a new session that follows a finished one, with Idempotency-Key  verifications:recheck
POST /v1/verify-api/sandbox/fixtures/{outcome}/sessions                create a session already at a simulated outcome verifications:create
POST /v1/verify-api/sandbox/sessions/{id}:simulate                     drive a sandbox session to a simulated outcome   verifications:create

Anyone (no sign-in): the contract a developer integrates against.
GET  /v1/verify-api/openapi.json                                       the partner-server operations as OpenAPI (CR-157)
GET  /v1/verify-api/sdks                                               the SDK release register and the button each package has earned (CR-173)
GET  /v1/verify-api/fixtures[/{persona}/plan], POST /v1/verify-api/fixtures/report   the sandbox persona catalog, delivery plans and a scored test report (CR-173)
POST /v1/verify-api/onboarding/webhook-confirmations                      your server confirms a delivered, verified webhook (CR-173)
GET  /v1/verify-api/credential-policy                                  what to rely on about rotation and revocation (CR-173)
GET  /v1/verify-api/versions[/{version}/openapi.json]                  the API versions and the immutable schema of each (CR-173)
POST /v1/verify-api/mcp, GET /v1/verify-api/mcp/registry             a read-only MCP server for the developer documentation (CR-173)
GET  /v1/verify-api/claims                                             what may be said publicly, built from evidence, and what may not (CR-173)
GET  /v1/verify-api/errors                                             the error classes a partner's server can meet and what to do about each (CR-173)

The person opening the launch link (no sign-in; the link is the grant):
GET  /v1/verify-api/hosted/{token}                                     who is asking and why, whether this is a test
POST /v1/verify-api/hosted/{token}:open                                the person opened it
POST /v1/verify-api/hosted/{token}:simulate                            a sandbox test page picks a simulated outcome
POST /v1/verify-api/hosted/{token}/consent                             a LIVE session: the person answers the notice (a decline ends the session, nothing photographed)
POST /v1/verify-api/hosted/{token}/nonce                               a LIVE session: a one-time value the next capture must carry
POST /v1/verify-api/hosted/{token}/capture                             a LIVE session: the document and live photographs; the answer is a retake with the tries left, or the end
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from zid_context import RequestContext, current_context
from zid_db import tenant_transaction
from zid_errors import ZidError

from zid_platform.api_auth import request_engine
from zid_platform.modules.verification.ports import partner_capture
from zid_platform.modules.verify_api.api.verify_api import VerifyApiDep
from zid_platform.modules.verify_api.application import (
    hosted_live,
    onboarding_service,
    partner_spec,
)
from zid_platform.modules.verify_api.application import sessions_service as svc
from zid_platform.modules.verify_api.application.throttle import FailureLimiter
from zid_platform.modules.verify_api.domain import api_versions as api_versions_rules
from zid_platform.modules.verify_api.domain import claims as claims_rules
from zid_platform.modules.verify_api.domain import credential_policy as credential_policy_rules
from zid_platform.modules.verify_api.domain import errors as error_catalog
from zid_platform.modules.verify_api.domain import fixtures, mcp_dev, people_prices, sdk_catalog
from zid_platform.modules.verify_api.domain import model as PartnerModel
from zid_platform.modules.verify_api.domain.model import PartnerError
from zid_platform.modules.verify_api.infrastructure import (
    api_version_register,
    people_price_register,
    sdk_register,
    store,
)

router = APIRouter(tags=["verify-api"])
_hosted_limiter = FailureLimiter()

_STATUS = {
    "AUTH_INVALID": 401,
    "TENANT_SCOPE_DENIED": 403,
    "ENVIRONMENT_MISMATCH": 403,
    "PRODUCT_NOT_ENTITLED": 403,
    "WORKFLOW_NOT_ENTITLED": 403,
    "MARKET_NOT_ENABLED": 403,
    "NOT_FOUND": 404,
    "DECISION_NOT_AVAILABLE": 404,
    "LAUNCH_INVALID": 404,
    "INVALID_IDEMPOTENCY_REUSE": 409,
    "SESSION_STATE_INVALID": 409,
    "VALIDATION_FAILED": 422,
    "WORKFLOW_VERSION_UNAVAILABLE": 422,
    "RETURN_ORIGIN_NOT_REGISTERED": 422,
    "RATE_LIMITED": 429,
    "IP_NOT_ALLOWED": 403,
    "SANDBOX_REQUIRES_SYNTHETIC_DATA": 422,
}


# answers only the person's own page can get (the live capture steps); a partner's server never sees them
_HOSTED_STATUS = {
    "CAPTURE_REFUSED": 422,
    "CAPTURE_UNAVAILABLE": 503,
}


def _fail(e: PartnerError) -> ZidError:
    status = _STATUS.get(e.code) or _HOSTED_STATUS.get(e.code, 422)
    return ZidError(e.code, e.detail, http_status=status, retryable=status >= 500)


def _no_store(body: dict[str, Any], status: int = 200) -> JSONResponse:
    return JSONResponse(
        body, status_code=status, headers={"Cache-Control": "no-store", "Pragma": "no-cache"}
    )


def _origin(request: Request) -> str:
    """The address the launch link is on: the console's own public address, from configuration, never from the request."""
    value = getattr(request.app.state, "verify_hosted_origin", None) or getattr(
        request.app.state, "public_base_url", None
    )
    if not value:
        raise ZidError(
            "DEPENDENCY_UNAVAILABLE",
            "The hosted verification address is not configured.",
            http_status=503,
        )
    return str(value)


def _tx(request: Request, who: Any) -> Any:
    """A database transaction for the company this token belongs to. The company comes from the token, never from the request."""
    base = current_context() or RequestContext("req_unknown", "cor_unknown", "zoiko-id", "unknown")
    return tenant_transaction(request_engine(request), base.with_identity(who.tenant_id, None))


# ------------------------------------------------------------------------------- the partner's server


class SessionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    customer_reference: str = Field(min_length=1, max_length=128)
    workflow_id: str | None = Field(default=None, max_length=64)
    workflow_version: str | None = Field(default=None, max_length=64)
    country: str = Field(min_length=2, max_length=2)
    region: str | None = Field(default=None, max_length=16)
    purpose_code: str = Field(max_length=64)
    locale: str | None = Field(default=None, max_length=10)
    return_origin: str | None = Field(default=None, max_length=200)
    metadata: dict[str, str] | None = None


class FixtureBody(BaseModel):
    """Every field is optional here: a test suite should not need to invent a person to see an outcome."""

    model_config = ConfigDict(extra="forbid")
    customer_reference: str | None = Field(default=None, min_length=1, max_length=128)
    workflow_id: str | None = Field(default=None, max_length=64)
    country: str | None = Field(default=None, min_length=2, max_length=2)
    region: str | None = Field(default=None, max_length=16)
    purpose_code: str | None = Field(default=None, max_length=64)
    locale: str | None = Field(default=None, max_length=10)
    metadata: dict[str, str] | None = None


class SandboxOutcomeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    outcome: str = Field(max_length=20)


class HostedConsentBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    consented: bool
    notice_version: str = Field(max_length=80)


_IMAGE_B64 = (6 * 1024 * 1024 * 4) // 3 + 16


class HostedCaptureBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    nonce: str = Field(min_length=8, max_length=128)
    captured_at: str = Field(max_length=40)
    image_base64: str = Field(min_length=8, max_length=_IMAGE_B64)
    selfie_base64: str = Field(min_length=8, max_length=_IMAGE_B64)


@router.get("/v1/verify-api/openapi.json")
async def partner_openapi(request: Request) -> JSONResponse:
    """The OpenAPI for a partner's server: only the operations its server calls, and only the schemas they use. Public, like any API reference; it describes the interface and holds no data."""
    return JSONResponse(
        partner_spec.build(request.app.openapi()), headers={"Cache-Control": "public, max-age=300"}
    )


@router.get("/v1/verify-api/errors")
async def partner_errors() -> JSONResponse:
    """Every error class a partner's server can meet, with what to do about each (ZID-CP-WF10 section 07.2). Public, like the API reference."""
    return JSONResponse(error_catalog.catalog_view(), headers={"Cache-Control": "public, max-age=300"})


@router.get("/v1/verify-api/credential-policy")
async def credential_policy() -> JSONResponse:
    """What a partner can rely on about credential lifetime, rotation overlap and how fast a revocation applies (ZID-CP-WF10, WF-10.04). A design statement, not a service objective. Public."""
    import os

    from zid_platform.modules.verify_api.application import observe

    raw = os.environ.get("ZID_VERIFY_REVOCATION_OBJECTIVE_SECONDS", "").strip()
    try:
        ratified = float(raw) if raw else None
    except ValueError:
        ratified = None
    hist = observe.REVOCATION_TO_REFUSAL
    series = hist.series.get(hist._key({}))
    count = int(series[-1]) if series else 0
    return JSONResponse(credential_policy_rules.policy(ratified, hist.quantile(0.99), count), headers={"Cache-Control": "public, max-age=60"})


@router.get("/v1/verify-api/versions")
async def api_versions() -> JSONResponse:
    """The partner API versions, their state, support dates, changelog and migration notes, each tied to an immutable schema snapshot (ZID-CP-WF10, WF-10.05). Public."""
    releases = [api_versions_rules.parse(x) for x in api_version_register.register()]
    return JSONResponse(api_versions_rules.view(releases), headers={"Cache-Control": "public, max-age=300"})


@router.get("/v1/verify-api/versions/{version}/openapi.json")
async def api_version_snapshot(version: str) -> JSONResponse:
    """The exact OpenAPI of one released version. It never changes after release: its digest is in the register."""
    for raw in api_version_register.register():
        r = api_versions_rules.parse(raw)
        if r.version == version:
            text = api_version_register.snapshot_text(r.snapshot)
            if text is None or api_versions_rules.snapshot_problems(r, text):
                raise ZidError("NOT_FOUND", "That version's schema is not available here.", http_status=404)
            return JSONResponse(api_versions_rules.load_json(text), headers={"Cache-Control": "public, max-age=86400", "ETag": '"' + r.digest + '"'})
    raise ZidError("NOT_FOUND", "There is no such API version.", http_status=404)


_mcp_limiter = FailureLimiter(window=60, limit=120)  # a public endpoint: at most 120 messages a minute from one address


def _mcp_tools() -> dict[str, Any]:
    def lookup(a: Any) -> Any:
        c = error_catalog.classify(str(a["code"]).upper())
        if c is None:
            raise KeyError(a["code"])
        return {"code": c.code, "http_status": c.http_status, "meaning": c.meaning, "caller_behavior": c.caller_behavior, "retryable": c.retryable}

    def personas(_: Any) -> Any:
        return fixtures.catalog_view()

    def plan(a: Any) -> Any:
        if fixtures.persona(str(a["persona"])) is None:
            raise KeyError(a["persona"])
        return fixtures.delivery_plan(str(a["persona"]), str(a.get("seed", "1")))

    def versions(_: Any) -> Any:
        return api_versions_rules.view([api_versions_rules.parse(x) for x in api_version_register.register()])

    def sdks(_: Any) -> Any:
        return sdk_catalog.view([sdk_catalog.parse(x) for x in sdk_register.register()], datetime.now(UTC).date())

    return {"lookup_error": lookup, "list_api_versions": versions, "list_sdks": sdks, "list_test_personas": personas, "get_test_plan": plan}


@router.post("/v1/verify-api/mcp")
async def developer_mcp(request: Request) -> Response:
    """A read-only MCP server for the developer documentation: error lookup, API versions, the SDK register and the sandbox personas. It needs no sign-in because it returns only what the public pages show, and it offers no tool that acts for a company (ZID-CP-WF10, WF-10.11)."""
    who = request.client.host if request.client else "unknown"
    wait = _mcp_limiter.blocked(who)
    if wait:
        raise ZidError("RATE_LIMITED", f"Too many messages. Try again in {wait} seconds.", http_status=429, retryable=True, retry_after_ms=int(wait) * 1000)
    _mcp_limiter.failed(who)  # every message counts toward the window
    raw = await request.body()
    if len(raw) > mcp_dev.MAX_REQUEST_BYTES:
        raise ZidError("VALIDATION_FAILED", "That message is too large.", http_status=413)
    try:
        message = json.loads(raw)
    except ValueError:
        return JSONResponse({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Not valid JSON."}})
    if not isinstance(message, dict):
        return JSONResponse({"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Send one JSON-RPC message at a time."}})
    answer = mcp_dev.handle(message, _mcp_tools())
    return Response(status_code=202) if answer is None else JSONResponse(answer, headers={"Cache-Control": "no-store"})


@router.get("/v1/verify-api/mcp/registry")
async def developer_mcp_registry() -> JSONResponse:
    """The tools this MCP server offers, and the ones that are deliberately off with the reason. Public."""
    return JSONResponse(mcp_dev.registry(), headers={"Cache-Control": "public, max-age=300"})


@router.get("/v1/verify-api/pricing/people")
async def people_pricing(plan: str | None = Query(default=None, pattern=r"^[a-z]{3,20}$"), verifications: int | None = Query(default=None, ge=0, le=1_000_000_000)) -> JSONResponse:
    """The Verify People prices as shown on the pricing page, and an estimate for a plan and a monthly volume. Proposed prices, not an invoice. Public."""
    table = people_price_register.table()
    out: dict[str, Any] = {
        "version": table.version, "status": table.status, "currency": table.currency,
        "plans": [{"code": p.code, "name": p.name, "custom": p.custom, "rate_per_verification": None if p.charge is None else format(p.charge, "f"), "monthly_minimum": None if p.minimum_monthly is None else format(p.minimum_monthly, "f")} for p in table.plans.values()],
    }  # fmt: skip
    if plan is not None and verifications is not None:
        try:
            out["estimate"] = people_prices.estimate(table, plan, verifications)
        except PartnerError as e:
            raise ZidError(e.code, e.detail, http_status=404 if e.code == "PLAN_UNKNOWN" else 422) from e
    return JSONResponse(out, headers={"Cache-Control": "public, max-age=60"})


@router.get("/v1/verify-api/claims")
async def public_claims() -> JSONResponse:
    """What may be said publicly about the developer platform, built from the evidence the platform holds, and what may not be said and why (ZID-CP-WF10, WF-10.12). Public."""
    from zid_platform.modules.verification.ports import certification_registry

    today = datetime.now(UTC).date()
    certs = [
        {"record_id": r.record_id, "scheme": r.scheme, "scheme_version": r.scheme_version, "country": r.country, "service_code": r.service_code, "certificate_ref": r.certificate_ref, "status": r.status.value, "valid_until": r.valid_until, "component_only": r.component_only}
        for r in certification_registry().records
        if r.entity == "ZOIKO"
    ]  # fmt: skip
    feed = claims_rules.build(
        sdk_view=sdk_catalog.view([sdk_catalog.parse(x) for x in sdk_register.register()], today),
        versions_view=api_versions_rules.view([api_versions_rules.parse(x) for x in api_version_register.register()]),
        persona_count=len(fixtures.PERSONAS), token_seconds=int(PartnerModel.TOKEN_TTL.total_seconds()), zoiko_certifications=certs, today=today,
    )  # fmt: skip
    return JSONResponse(feed, headers={"Cache-Control": "public, max-age=300"})


@router.get("/v1/verify-api/sdks")
async def partner_sdks() -> JSONResponse:
    """The SDK release register: what each package is, whether it is published, signed and tested, and the strongest button it has earned (ZID-CP-WF10, WF-10.06). Public."""
    today = datetime.now(UTC).date()
    out = sdk_catalog.view([sdk_catalog.parse(x) for x in sdk_register.register()], today)
    return JSONResponse(out, headers={"Cache-Control": "public, max-age=300"})


@router.get("/v1/verify-api/fixtures")
async def fixture_catalog() -> JSONResponse:
    """The sandbox persona catalog: fixed, versioned, synthetic scenarios including the awkward ones (ZID-CP-WF10, WF-10.08). Public."""
    return JSONResponse(fixtures.catalog_view(), headers={"Cache-Control": "public, max-age=300"})


@router.get("/v1/verify-api/fixtures/{persona_id}/plan")
async def fixture_plan(persona_id: str, seed: str = Query(default="1", pattern=r"^[A-Za-z0-9_.-]{1,40}$")) -> JSONResponse:
    """How the events of one persona are to be delivered to a partner's endpoint, and what a correct server must do. The same persona and seed always give the same plan. Public."""
    if fixtures.persona(persona_id) is None:
        raise ZidError("NOT_FOUND", "There is no such test persona.", http_status=404)
    return JSONResponse({**fixtures.delivery_plan(persona_id, seed), "expectations": fixtures.expectations(persona_id)}, headers={"Cache-Control": "no-store"})


class FixtureObservationBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    persona: str = Field(max_length=60)
    seed: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,40}$")
    plan_digest: str = Field(max_length=100)
    signatures_refused: int = Field(default=0, ge=0, le=1000)
    acted_on_event_ids: list[str] = Field(default_factory=list, max_length=200)
    final_outcome_seen: str | None = Field(default=None, max_length=40)
    read_authoritative_state: bool = False
    acted_on_pending: bool = False
    refused_missing_field: bool = False
    accepted_extra_field: bool = False


class FixtureReportBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    observations: list[FixtureObservationBody] = Field(min_length=1, max_length=40)


@router.post("/v1/verify-api/fixtures/report")
async def fixture_report(body: FixtureReportBody) -> JSONResponse:
    """Score what a partner's server did against the catalog. Stateless and public: nothing is stored. It is a test result, never a certification or a go-live approval."""
    out = fixtures.report([fixtures.parse_observation(o.model_dump()) for o in body.observations])
    return JSONResponse(out, headers={"Cache-Control": "no-store"})


class WebhookConfirmBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_id: str = Field(min_length=8, max_length=64)


@router.post("/v1/verify-api/onboarding/webhook-confirmations", status_code=201)
async def confirm_webhook(body: WebhookConfirmBody, request: Request, who: VerifyApiDep) -> dict[str, Any]:
    """Your server says it received a delivered webhook and verified its signature, naming the event id. The last step of the first-verification checklist (ZID-CP-WF10, WF-10.01)."""
    try:
        who.require("webhooks:read")
        async with _tx(request, who) as conn:
            return await onboarding_service.confirm_webhook(conn, who.tenant_id, who.application_id, body.event_id)
    except PartnerError as e:
        raise _fail(e) from e


@router.get("/v1/verify-api/workflows")
async def workflows(request: Request, who: VerifyApiDep) -> dict[str, Any]:
    try:
        who.require("workflows:read")
        async with _tx(request, who) as conn:
            app = await store.get_application(conn, who.application_id)
    except PartnerError as e:
        raise _fail(e) from e
    return svc.catalogue(app)


@router.get("/v1/verify-api/products")
async def products(who: VerifyApiDep) -> dict[str, Any]:
    try:
        who.require("products:read")
    except PartnerError as e:
        raise _fail(e) from e
    from zid_platform.modules.verify_api.domain.sessions import PRODUCTS

    return {"items": PRODUCTS}


@router.post("/v1/verify-api/verification-sessions", status_code=201)
async def create_session(
    body: SessionBody,
    request: Request,
    who: VerifyApiDep,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> JSONResponse:
    try:
        async with _tx(request, who) as conn:
            view, created = await svc.create_session(
                conn,
                who,
                key=idempotency_key,
                body=body.model_dump(exclude_none=True),
                origin=_origin(request),
            )
    except PartnerError as e:
        raise _fail(e) from e
    request.state.log_session_id = str(view.get("session_id", "")) or None  # lets the request log tie this call to the session it made
    return _no_store(view, 201 if created else 200)


@router.get("/v1/verify-api/verification-sessions")
async def list_sessions(
    request: Request,
    who: VerifyApiDep,
    status: str | None = Query(default=None, max_length=24),
    customer_reference: str | None = Query(default=None, max_length=128),
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    cursor: str | None = Query(default=None, max_length=300),
    limit: int = Query(default=25, ge=1, le=100),
) -> dict[str, Any]:
    try:
        async with _tx(request, who) as conn:
            return await svc.list_sessions(
                conn,
                who,
                status=status,
                customer_reference=customer_reference,
                created_from=created_from,
                created_to=created_to,
                cursor=cursor,
                limit=limit,
            )
    except PartnerError as e:
        raise _fail(e) from e


@router.get("/v1/verify-api/verification-sessions/{session_id}")
async def get_session(session_id: str, request: Request, who: VerifyApiDep) -> dict[str, Any]:
    try:
        async with _tx(request, who) as conn:
            return await svc.get_session(conn, who, session_id)
    except PartnerError as e:
        raise _fail(e) from e


@router.get("/v1/verify-api/verification-sessions/{session_id}/decision")
async def get_decision(session_id: str, request: Request, who: VerifyApiDep) -> dict[str, Any]:
    try:
        async with _tx(request, who) as conn:
            return await svc.get_decision(conn, who, session_id)
    except PartnerError as e:
        raise _fail(e) from e


@router.post("/v1/verify-api/verification-sessions/{session_id}:cancel")
async def cancel_session(session_id: str, request: Request, who: VerifyApiDep) -> dict[str, Any]:
    try:
        async with _tx(request, who) as conn:
            return await svc.cancel_session(conn, who, session_id)
    except PartnerError as e:
        raise _fail(e) from e


@router.post("/v1/verify-api/verification-sessions/{session_id}/resume-token")
async def resume_token(session_id: str, request: Request, who: VerifyApiDep) -> JSONResponse:
    try:
        async with _tx(request, who) as conn:
            view = await svc.resume_token(conn, who, session_id, origin=_origin(request))
    except PartnerError as e:
        raise _fail(e) from e
    return _no_store(view)


@router.post("/v1/verify-api/verification-sessions/{session_id}:recheck", status_code=201)
async def recheck_session(
    session_id: str,
    request: Request,
    who: VerifyApiDep,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> JSONResponse:
    try:
        async with _tx(request, who) as conn:
            view, created = await svc.recheck_session(
                conn, who, session_id, key=idempotency_key, origin=_origin(request)
            )
    except PartnerError as e:
        raise _fail(e) from e
    return _no_store(view, 201 if created else 200)


@router.post("/v1/verify-api/sandbox/fixtures/{outcome}/sessions", status_code=201)
async def fixture_session(
    outcome: str,
    body: FixtureBody,
    request: Request,
    who: VerifyApiDep,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> JSONResponse:
    try:
        async with _tx(request, who) as conn:
            result = await svc.fixture_session(
                conn,
                who,
                outcome,
                key=idempotency_key,
                body=body.model_dump(exclude_none=True),
                origin=_origin(request),
            )
    except PartnerError as e:
        raise _fail(e) from e
    return _no_store(result, 201 if result["created"] else 200)


@router.post("/v1/verify-api/sandbox/sessions/{session_id}:simulate")
async def simulate_session(
    session_id: str, body: SandboxOutcomeBody, request: Request, who: VerifyApiDep
) -> dict[str, Any]:
    try:
        async with _tx(request, who) as conn:
            return await svc.simulate_for_partner(conn, who, session_id, body.outcome)
    except PartnerError as e:
        raise _fail(e) from e


# ------------------------------------------------------------------------------- the person opening the link


async def _hosted(request: Request, token: str, fn: Any) -> JSONResponse:
    """One answer for every wrong link, and a limit on wrong ones from one address, so links cannot be guessed. The link is the only thing the person holds."""
    who = request.client.host if request.client else "unknown"
    wait = _hosted_limiter.blocked(who)
    if wait:
        raise ZidError(
            "RATE_LIMITED",
            f"Too many attempts. Try again in {wait} seconds.",
            http_status=429,
            retryable=True,
            retry_after_ms=int(wait) * 1000,
            headers={"Retry-After": str(int(wait))},
        )
    try:
        async with request_engine(request).begin() as conn, store.auth_scope(conn):
            out = await fn(conn)
    except PartnerError as e:
        if e.code == "LAUNCH_INVALID":
            _hosted_limiter.failed(who)
        raise _fail(e) from e
    return _no_store(out)


@router.get("/v1/verify-api/hosted/{token}")
async def hosted_context(token: str, request: Request) -> JSONResponse:
    sv = partner_capture.services_from_state(request.app.state)

    async def go(conn: Any) -> dict[str, Any]:
        out = await svc.hosted_context(conn, token)
        return (
            {**out, **(await hosted_live.extras_for(conn, token, sv))} if out.get("live") else out
        )

    return await _hosted(request, token, go)


@router.post("/v1/verify-api/hosted/{token}:open")
async def hosted_open(token: str, request: Request) -> JSONResponse:
    sv = partner_capture.services_from_state(request.app.state)

    async def go(conn: Any) -> dict[str, Any]:
        out = await svc.hosted_open(conn, token)
        return (
            {**out, **(await hosted_live.extras_for(conn, token, sv))} if out.get("live") else out
        )

    return await _hosted(request, token, go)


@router.post("/v1/verify-api/hosted/{token}/consent")
async def hosted_consent(token: str, body: HostedConsentBody, request: Request) -> JSONResponse:
    sv = partner_capture.services_from_state(request.app.state)
    return await _hosted(
        request,
        token,
        lambda conn: hosted_live.consent(
            conn, token, sv, notice_version=body.notice_version, consented=body.consented
        ),
    )


@router.post("/v1/verify-api/hosted/{token}/nonce")
async def hosted_nonce(token: str, request: Request) -> JSONResponse:
    sv = partner_capture.services_from_state(request.app.state)
    return await _hosted(request, token, lambda conn: hosted_live.nonce(conn, token, sv))


@router.post("/v1/verify-api/hosted/{token}/capture")
async def hosted_capture(token: str, body: HostedCaptureBody, request: Request) -> JSONResponse:
    sv = partner_capture.services_from_state(request.app.state)
    return await _hosted(
        request,
        token,
        lambda conn: hosted_live.capture(
            conn,
            token,
            sv,
            nonce=body.nonce,
            captured_at=body.captured_at,
            image_base64=body.image_base64,
            selfie_base64=body.selfie_base64,
        ),
    )


@router.post("/v1/verify-api/hosted/{token}:simulate")
async def hosted_simulate(token: str, body: SandboxOutcomeBody, request: Request) -> JSONResponse:
    return await _hosted(
        request, token, lambda conn: svc.hosted_simulate(conn, token, body.outcome)
    )
