"""Shared fixtures. Tests run against the real content snapshot (sample) with in-memory stores unless a test says otherwise."""

from __future__ import annotations

import logging
from collections.abc import Iterator

import pytest
from assistant_helpers import H, P, make_settings
from fastapi.testclient import TestClient
from zid_assistant.application.pipeline import AssistantPipeline
from zid_assistant.application.snapshot import Knowledge, load_knowledge
from zid_assistant.infrastructure.sessions import MemorySessionStore
from zid_assistant.infrastructure.store import MemoryStore
from zid_assistant.main import create_app
from zid_assistant.settings import AssistantSettings


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _quiet_logs() -> Iterator[None]:
    logging.disable(logging.CRITICAL)
    yield
    logging.disable(logging.NOTSET)


@pytest.fixture(scope="session")
def knowledge() -> Knowledge:
    return load_knowledge(make_settings())


@pytest.fixture
def settings() -> AssistantSettings:
    return make_settings()


@pytest.fixture
def pipeline(knowledge: Knowledge, settings: AssistantSettings) -> AssistantPipeline:
    return AssistantPipeline(knowledge, settings, None)


@pytest.fixture
def store() -> MemoryStore:
    return MemoryStore()


@pytest.fixture
def client(settings: AssistantSettings, store: MemoryStore) -> Iterator[TestClient]:
    app = create_app(settings, store=store, sessions=MemorySessionStore(1800, 7200))
    with TestClient(app, base_url="http://localhost:8210") as c:
        yield c


@pytest.fixture
def session_client(client: TestClient) -> TestClient:
    assert client.post(f"{P}/sessions", headers=H).status_code == 201
    return client
