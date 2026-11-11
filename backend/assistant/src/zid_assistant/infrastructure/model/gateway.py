"""Gateway around a provider: daily budget, circuit breaker, deadline (ADR-05, OD-017). Never infinite retries."""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime

from zid_assistant.settings import AssistantSettings

from .base import DraftRequest, DraftResult, ModelProvider, ModelUnavailable
from .claude import ClaudeProvider
from .http_providers import GeminiProvider, GroqProvider
from .mock import MockProvider


class ModelGateway:
    def __init__(
        self,
        provider: ModelProvider,
        settings: AssistantSettings,
        clock: Callable[[], float] = time.monotonic,
        today: Callable[[], str] = lambda: datetime.now(UTC).strftime("%Y-%m-%d"),
    ) -> None:
        self._provider = provider
        self._s = settings
        self._clock = clock
        self._today = today
        self._failures = 0
        self._open_until = 0.0
        self._day = today()
        self._calls = 0

    @property
    def provider_name(self) -> str:
        return self._provider.name

    @property
    def breaker_open(self) -> bool:
        return self._clock() < self._open_until

    async def draft(self, request: DraftRequest) -> DraftResult:
        now_day = self._today()
        if now_day != self._day:
            self._day, self._calls = now_day, 0
        if self.breaker_open:
            raise ModelUnavailable("breaker_open")
        if self._calls >= self._s.model_daily_request_budget:
            raise ModelUnavailable("budget_exhausted")
        self._calls += 1
        try:
            result = await self._provider.draft(request)
        except ModelUnavailable:
            self._failures += 1
            if self._failures >= self._s.model_breaker_failures:
                self._open_until = self._clock() + self._s.model_breaker_cooldown_seconds
                self._failures = 0
            raise
        self._failures = 0
        return result


def build_gateway(settings: AssistantSettings) -> ModelGateway | None:
    """The gateway for the configured provider, or None when it cannot be built (for example no key file yet). Whether AI answers are
    used is decided per request by the runtime `ai_answers` flag, so an operator can switch them off without a restart (INV-07)."""
    provider: ModelProvider
    try:
        if settings.model_provider == "claude":
            provider = ClaudeProvider(settings)
        elif settings.model_provider == "groq":
            provider = GroqProvider(settings)
        elif settings.model_provider == "gemini":
            provider = GeminiProvider(settings)
        elif settings.model_provider == "mock":
            provider = MockProvider()
        else:
            return None
    except ModelUnavailable:
        return None
    return ModelGateway(provider, settings)
