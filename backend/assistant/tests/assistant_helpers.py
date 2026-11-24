"""Plain helpers shared by the assistant tests (importable because tests/ is on sys.path via conftest)."""

from __future__ import annotations

from dataclasses import replace

from zid_assistant.application.snapshot import Knowledge
from zid_assistant.settings import AssistantSettings

ORIGIN = "https://www.zoikoid.example"
P = "/api/public/chat/v1"
H = {"origin": ORIGIN}


def make_settings(**kw: object) -> AssistantSettings:
    base: dict[str, object] = {"enabled": True, "environment": "test", "allowed_origins": [ORIGIN]}
    base.update(kw)
    return AssistantSettings(_env_file=None, **base)  # type: ignore[arg-type]


def withdraw(
    knowledge: Knowledge,
    *,
    sources: tuple[str, ...] = (),
    links: tuple[str, ...] = (),
    capabilities: tuple[str, ...] = (),
) -> Knowledge:
    """A copy of the knowledge with a revocation watermark (ADR-11), as the publication service would produce."""
    snap = replace(
        knowledge.snapshot,
        withdrawn_sources=frozenset(sources),
        withdrawn_links=frozenset(links),
        withdrawn_capabilities=frozenset(capabilities),
    )
    return replace(knowledge, snapshot=snap)
