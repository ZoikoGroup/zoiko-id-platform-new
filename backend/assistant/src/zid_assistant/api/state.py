"""The assembled service state, held on `app.state.assistant`. Built once at startup by `main.create_app`."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from zid_assistant.application.intake import IntakeService
from zid_assistant.application.pipeline import AssistantPipeline
from zid_assistant.application.snapshot import Knowledge
from zid_assistant.infrastructure.flags import FlagStore
from zid_assistant.infrastructure.ratelimit import RateLimiter
from zid_assistant.infrastructure.revocations import RevocationStore
from zid_assistant.infrastructure.sessions import SessionStore
from zid_assistant.infrastructure.store import Store
from zid_assistant.settings import AssistantSettings


@dataclass
class AssistantState:
    settings: AssistantSettings
    knowledge: Knowledge
    pipeline: AssistantPipeline
    sessions: SessionStore
    limiter: RateLimiter
    flags: FlagStore
    revocations: RevocationStore
    store: Store
    intake: IntakeService
    background: list[asyncio.Task[None]] = field(default_factory=list)
    stop: asyncio.Event = field(default_factory=asyncio.Event)
