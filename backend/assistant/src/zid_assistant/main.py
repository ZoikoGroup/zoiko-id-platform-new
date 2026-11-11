"""App factory for the public assistant service (ZI-CHAT-ADR-006 ADR-02): an isolated deployable with its own process, schema and port.

It reuses only the platform's service shell (`zid-app`: health, request context, error envelope, metrics) and never imports
`zid_platform` (tests/security/test_isolation.py). It holds no identity, tenant, token or signing-key setting.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from zid_app import Journey, ServiceSettings, ServiceSpec, create_service_app

from zid_assistant import API_PREFIX
from zid_assistant.api.errors import ApiError, error_response
from zid_assistant.api.routes import router
from zid_assistant.api.state import AssistantState
from zid_assistant.application.intake import IntakeCrypto, IntakeService
from zid_assistant.application.pipeline import AssistantPipeline
from zid_assistant.application.snapshot import Knowledge, load_knowledge
from zid_assistant.infrastructure.delivery import OutboxWorker, build_adapters, worker_loop
from zid_assistant.infrastructure.flags import FlagStore
from zid_assistant.infrastructure.model.base import ModelProvider
from zid_assistant.infrastructure.model.gateway import ModelGateway, build_gateway
from zid_assistant.infrastructure.ratelimit import RateLimiter
from zid_assistant.infrastructure.revocations import RevocationStore
from zid_assistant.infrastructure.sessions import (
    MemorySessionStore,
    SessionStore,
    ValkeySessionStore,
)
from zid_assistant.infrastructure.store import MemoryStore, SqlStore, Store
from zid_assistant.settings import AssistantSettings

SPEC = ServiceSpec(
    name="public-assistant",
    plane="public",
    modules=("assistant",),
    required_schemas=("public_assistant",),
    journeys=(
        Journey("assistant-messages", f"{API_PREFIX}/messages", 15.0),
        Journey("assistant-bootstrap", f"{API_PREFIX}/bootstrap", 1.0),
    ),
)


def _notices(settings: AssistantSettings, k: Knowledge) -> dict[str, tuple[str, str]]:
    out: dict[str, tuple[str, str]] = {}
    if settings.intake_notice_sales_id:
        out["SALES"] = (
            settings.intake_notice_sales_id,
            k.copy_text(
                "intake_notice_sales",
                "We use the details you enter only to respond to this request. Your chat is not shared.",
            ),
        )
    if settings.intake_notice_support_id:
        out["SUPPORT"] = (
            settings.intake_notice_support_id,
            k.copy_text(
                "intake_notice_support",
                "We use the details you enter only to respond to this request. Your chat is not shared.",
            ),
        )
    return out


def build_state(
    settings: AssistantSettings,
    *,
    knowledge: Knowledge | None = None,
    store: Store | None = None,
    sessions: SessionStore | None = None,
    provider: ModelProvider | None = None,
) -> AssistantState:
    k = knowledge or load_knowledge(settings)
    if store is None:
        store = SqlStore(settings.database_url) if settings.database_url else MemoryStore()
    if sessions is None:
        sessions = (
            ValkeySessionStore(
                settings.valkey_url,
                settings.session_idle_seconds,
                settings.session_absolute_seconds,
            )
            if settings.valkey_url
            else MemorySessionStore(
                settings.session_idle_seconds, settings.session_absolute_seconds
            )
        )
    gateway = ModelGateway(provider, settings) if provider is not None else build_gateway(settings)
    pipeline = AssistantPipeline(k, settings, gateway)
    crypto = IntakeCrypto(settings)
    return AssistantState(
        settings=settings,
        knowledge=k,
        pipeline=pipeline,
        sessions=sessions,
        limiter=RateLimiter(settings.rate_limit_window_seconds),
        flags=FlagStore(settings),
        revocations=RevocationStore(settings),
        store=store,
        intake=IntakeService(store, settings, crypto, _notices(settings, k)),
    )


def create_app(settings: AssistantSettings | None = None, **overrides: object) -> FastAPI:
    settings = settings or AssistantSettings()
    state = build_state(settings, **overrides)  # type: ignore[arg-type]
    shell = ServiceSettings(
        service_name=SPEC.name,
        environment=settings.environment,
        cell_id=settings.cell_id,
        log_level=settings.log_level,
        database_url=settings.database_url,
    )
    app = create_service_app(SPEC, shell)
    app.state.assistant = state
    app.add_exception_handler(ApiError, error_response)  # type: ignore[arg-type]
    app.include_router(router)

    inner = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(a: FastAPI) -> AsyncIterator[None]:
        async with inner(a):
            worker = OutboxWorker(
                state.store,
                state.intake._crypto,
                build_adapters(settings),
                settings.outbox_max_attempts,
            )
            state.background.append(asyncio.create_task(worker_loop(worker, state.stop)))
            try:
                yield
            finally:
                state.stop.set()
                for t in state.background:
                    t.cancel()
                if isinstance(state.store, SqlStore):
                    await state.store.dispose()

    app.router.lifespan_context = lifespan
    return app
