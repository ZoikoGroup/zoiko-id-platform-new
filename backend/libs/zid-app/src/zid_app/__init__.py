"""FastAPI service shell shared by every Zoiko iD role and process (DEC-D6).

Provides: typed settings, request-context and trace-context middleware, canonical error envelopes,
structured request logging, request metrics and journey SLIs (SRE-001 §4), health endpoints
(`/health/live`, `/health/ready`, `/health/startup`, `/`) and the cell-internal Prometheus scrape
endpoint `/internal/metrics` (the ingress never routes `/internal/*` from outside the cell).
Domain modules plug their routers in; this package holds no domain logic.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.ext.asyncio import AsyncEngine
from starlette.exceptions import HTTPException as StarletteHTTPException
from zid_context import RequestContext, current_context, new_id, reset_context, set_context
from zid_db import CheckResult, check_database, create_engine
from zid_errors import (
    INTERNAL_ERROR,
    NOT_FOUND,
    VALIDATION_FAILED,
    ErrorBody,
    ErrorDetail,
    ErrorEnvelope,
    Meta,
    SuccessEnvelope,
    ZidError,
)
from zid_telemetry import configure_logging, get_logger
from zid_telemetry.metrics import REGISTRY, Counter, Histogram, Registry
from zid_telemetry.slo import WindowedSli
from zid_telemetry.tracing import reset_trace, set_trace, start_trace

__all__ = ["Journey", "ServiceSettings", "ServiceSpec", "create_service_app", "envelope"]

_log = get_logger("zid.request")


class ServiceSettings(BaseSettings):
    """Non-secret defaults only. Secrets arrive at runtime from the secrets broker (DEC-F5)."""

    model_config = SettingsConfigDict(env_prefix="ZID_", extra="ignore")

    service_name: str = "unnamed"
    service_version: str = "0.1.0"
    environment: str = "local"
    cell_id: str = "local-1"
    log_level: str = "INFO"
    database_url: str | None = None


class Journey:
    """A critical user journey measured as its own SLI (SRE-001 §4: per journey, not one blended
    platform percentile): requests whose path starts with `prefix`, good when not a server error
    and answered within `latency_s`."""

    def __init__(self, name: str, prefix: str, latency_s: float) -> None:
        self.name = name
        self.prefix = prefix
        self.latency_s = latency_s


class ServiceSpec:
    """What a deployable unit declares about itself."""

    def __init__(
        self,
        name: str,
        plane: str,
        modules: Sequence[str],
        required_schemas: Sequence[str],
        journeys: Sequence[Journey] = (),
    ) -> None:
        self.name = name
        self.plane = plane
        self.modules = tuple(modules)
        self.required_schemas = tuple(required_schemas)
        self.journeys = tuple(sorted(journeys, key=lambda j: -len(j.prefix)))  # longest prefix wins

    def journey(self, path: str) -> Journey | None:
        return next((j for j in self.journeys if path.startswith(j.prefix)), None)


class JourneySlis:
    """Per journey: availability (non-5xx / all: a caller-caused 4xx is a good event for the
    service, SRE-001 §4) and latency (answered without a server error within threshold / all)."""

    def __init__(self, journeys: Sequence[Journey], registry: Registry = REGISTRY) -> None:
        self.availability = {j.name: WindowedSli() for j in journeys}
        self.latency = {j.name: WindowedSli() for j in journeys}
        # the same good/bad events for Prometheus burn-rate rules (one definition, two consumers)
        self.events = registry.counter(
            "zid_sli_events_total",
            "SLI events by journey, SLI and outcome",
            ("journey", "sli", "outcome"),
        )

    def record(self, journey: Journey, status: int, seconds: float) -> None:
        available = status < 500
        fast = available and seconds <= journey.latency_s
        self.availability[journey.name].record(available)
        self.latency[journey.name].record(fast)
        self.events.inc(
            journey=journey.name, sli="availability", outcome="good" if available else "bad"
        )
        self.events.inc(journey=journey.name, sli="latency", outcome="good" if fast else "bad")


def _instruments(registry: Registry) -> tuple[Counter, Histogram]:
    requests = registry.counter(
        "zid_http_requests_total",
        "HTTP requests by journey, method and status class",
        ("journey", "method", "status_class"),
    )
    duration = registry.histogram(
        "zid_http_request_duration_seconds", "Server processing time by journey", ("journey",)
    )
    return requests, duration


def _ctx() -> RequestContext:
    ctx = current_context()
    assert ctx is not None, "request context middleware not installed"
    return ctx


def envelope(data: object) -> dict[str, object]:
    """Wrap a successful payload in the canonical envelope (ZID-API-001 §3.1)."""
    ctx = _ctx()
    return SuccessEnvelope(
        request_id=ctx.request_id,
        correlation_id=ctx.correlation_id,
        data=data,
        meta=Meta(service=ctx.service, region=ctx.cell_id, tenant_id=ctx.tenant_id),
    ).model_dump()


def _error_response(
    status: int, body: ErrorBody, headers: dict[str, str] | None = None
) -> JSONResponse:
    ctx = current_context()
    env = ErrorEnvelope(
        request_id=ctx.request_id if ctx else "req_unknown",
        correlation_id=ctx.correlation_id if ctx else "cor_unknown",
        error=body,
    )
    return JSONResponse(env.model_dump(), status_code=status, headers=headers)


def create_service_app(
    spec: ServiceSpec,
    settings: ServiceSettings,
    extra_checks: Sequence[Callable[[], Awaitable[CheckResult]]] = (),
) -> FastAPI:
    configure_logging(spec.name, settings.cell_id, settings.log_level)
    requests_total, request_duration = _instruments(REGISTRY)
    slis = JourneySlis(spec.journeys)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine: AsyncEngine | None = (
            create_engine(settings.database_url) if settings.database_url else None
        )
        app.state.engine = engine
        app.state.started = True
        get_logger("zid.lifecycle").info("service started", extra={"component": "lifecycle"})
        yield
        if engine is not None:
            await engine.dispose()

    app = FastAPI(
        title=f"Zoiko iD — {spec.name}", version=settings.service_version, lifespan=lifespan
    )
    app.state.started = False
    app.state.journey_slis = slis
    app.state.service_spec = spec

    # ---------------- middleware: request context + request log ----------------
    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[object]]
    ) -> object:
        request_id = new_id("req")  # always generated server-side
        incoming = request.headers.get("x-correlation-id", "")
        correlation_id = (
            incoming if 0 < len(incoming) <= 128 and incoming.isprintable() else new_id("cor")
        )
        token = set_context(
            RequestContext(
                request_id=request_id,
                correlation_id=correlation_id,
                service=spec.name,
                cell_id=settings.cell_id,
            )
        )
        trace = start_trace(request.headers.get("traceparent"))
        trace_token = set_trace(trace)
        started = time.perf_counter()
        try:
            response = await call_next(request)
            response.headers["X-Request-Id"] = request_id  # type: ignore[attr-defined]
            response.headers["X-Correlation-Id"] = correlation_id  # type: ignore[attr-defined]
            response.headers["traceparent"] = trace.traceparent()  # type: ignore[attr-defined]
            elapsed = time.perf_counter() - started
            status = int(response.status_code)  # type: ignore[attr-defined]
            journey = spec.journey(request.url.path)
            name = journey.name if journey else "other"
            requests_total.inc(
                journey=name, method=request.method, status_class=f"{status // 100}xx"
            )
            request_duration.observe(elapsed, journey=name)
            if journey is not None:
                slis.record(journey, status, elapsed)
            _log.info(
                "request",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status": response.status_code,  # type: ignore[attr-defined]
                    "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                },
            )
            return response
        finally:
            reset_trace(trace_token)
            reset_context(token)

    # ---------------- error handlers: always the canonical envelope ----------------
    @app.exception_handler(ZidError)
    async def zid_error(_: Request, exc: ZidError) -> JSONResponse:
        headers = dict(exc.headers)
        if exc.retryable:
            headers["Retry-After"] = str(max(1, (exc.retry_after_ms or 0) // 1000))
        return _error_response(exc.http_status, exc.body(), headers or None)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        details = [
            ErrorDetail(
                field=".".join(str(p) for p in e.get("loc", ())), issue=e.get("type", "invalid")
            )
            for e in exc.errors()
        ]
        return _error_response(
            422, ErrorBody(code=VALIDATION_FAILED, message="Request is invalid.", details=details)
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = NOT_FOUND if exc.status_code == 404 else f"HTTP_{exc.status_code}"
        message = (
            "Resource not found." if exc.status_code == 404 else "Request could not be processed."
        )
        return _error_response(exc.status_code, ErrorBody(code=code, message=message))

    @app.exception_handler(Exception)
    async def unhandled(_: Request, exc: Exception) -> JSONResponse:
        get_logger("zid.error").error("unhandled error", exc_info=exc, extra={"outcome": "error"})
        return _error_response(
            500, ErrorBody(code=INTERNAL_ERROR, message="An internal error occurred.")
        )

    # ---------------- health and info ----------------
    @app.get("/health/live", tags=["health"], summary="Process is up")
    async def live() -> dict[str, object]:
        return envelope({"status": "live"})

    @app.get(
        "/health/ready", tags=["health"], summary="Dependencies reachable; safe to receive traffic"
    )
    async def ready(request: Request) -> JSONResponse:
        results = [await check_database(request.app.state.engine, spec.required_schemas)]
        results += [await check() for check in extra_checks]
        ok = all(r.ok for r in results)
        data = {
            "status": "ready" if ok else "not_ready",
            "checks": [{"name": r.name, "ok": r.ok, "detail": r.detail} for r in results],
        }
        return JSONResponse(envelope(data), status_code=200 if ok else 503)

    @app.get("/health/startup", tags=["health"], summary="Startup completed (lifespan ran)")
    async def startup(request: Request) -> JSONResponse:
        ok = bool(request.app.state.started)
        return JSONResponse(
            envelope({"status": "started" if ok else "starting"}), status_code=200 if ok else 503
        )

    @app.get("/internal/metrics", tags=["health"], summary="Prometheus scrape (cell-internal)")
    async def metrics() -> PlainTextResponse:
        return PlainTextResponse(
            REGISTRY.exposition(), media_type="text/plain; version=0.0.4; charset=utf-8"
        )

    @app.get("/", tags=["health"], summary="Service identity")
    async def info() -> dict[str, object]:
        return envelope(
            {
                "service": spec.name,
                "plane": spec.plane,
                "version": settings.service_version,
                "environment": settings.environment,
                "cell_id": settings.cell_id,
                "modules": list(spec.modules),
                "owned_schemas": list(spec.required_schemas),
            }
        )

    return app
