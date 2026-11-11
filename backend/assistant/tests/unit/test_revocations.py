"""Runtime withdrawal (ADR-11, B07): a pulled source, link or capability stops being served on the next request, on every replica, with no deploy."""

from __future__ import annotations

import pytest
from assistant_helpers import H, P, make_settings
from fastapi.testclient import TestClient
from zid_assistant.application.snapshot import Knowledge
from zid_assistant.infrastructure.revocations import Revocations, RevocationStore, apply
from zid_assistant.infrastructure.sessions import MemorySessionStore
from zid_assistant.infrastructure.store import MemoryStore
from zid_assistant.main import create_app

ASK = "What is Zoiko iD?"


class Clock:
    t = 0.0

    def __call__(self) -> float:
        return self.t


def client() -> TestClient:
    app = create_app(make_settings(), store=MemoryStore(), sessions=MemorySessionStore(1800, 7200))
    c = TestClient(app, base_url="http://localhost:8210")
    c.__enter__()
    assert c.post(f"{P}/sessions", headers=H).status_code == 201
    return c


def ask(c: TestClient, mid: str, text: str = ASK) -> dict[str, object]:
    r = c.post(f"{P}/messages", headers=H, json={"client_message_id": mid, "text": text})
    assert r.status_code == 200
    return r.json()  # type: ignore[no-any-return]


def revocations(c: TestClient) -> RevocationStore:
    return c.app.state.assistant.revocations  # type: ignore[attr-defined,no-any-return]


@pytest.mark.anyio
async def test_revoke_restore_and_validation() -> None:
    store = RevocationStore(make_settings(), ttl=0)
    assert not await store.get()
    await store.revoke("source", "PUB-001")
    await store.revoke("link", "OVERVIEW")
    await store.revoke("capability", "CAP-001")
    r = await store.get()
    assert r.sources == {"PUB-001"} and r.links == {"OVERVIEW"} and r.capabilities == {"CAP-001"}
    await store.restore("source", "PUB-001")
    assert not (await store.get()).sources
    for kind, ident in (("page", "x"), ("source", ""), ("source", "a:b"), ("source", "x" * 81)):
        with pytest.raises(ValueError):
            await store.revoke(kind, ident)


@pytest.mark.anyio
async def test_the_list_is_cached_briefly_but_a_local_change_is_instant() -> None:
    clock = Clock()
    store = RevocationStore(make_settings(), clock=clock, ttl=5.0)
    await store.get()
    await store.revoke("source", "PUB-9")
    assert (await store.get()).sources == {"PUB-9"}, "the instance that changed it sees it at once"


def test_apply_folds_into_the_watermark_without_copying_the_registers(knowledge: Knowledge) -> None:
    k = apply(knowledge, Revocations(sources=frozenset({"PUB-001"}), links=frozenset({"OVERVIEW"})))
    assert k.snapshot.withdrawn_sources == {"PUB-001"} and k.snapshot.withdrawn_links == {
        "OVERVIEW"
    }
    assert k.snapshot.sources is knowledge.snapshot.sources, "registers are shared, not copied"
    assert apply(knowledge, Revocations()) is knowledge, "nothing revoked: no work"
    assert not knowledge.snapshot.withdrawn_sources, "the signed snapshot itself is never mutated"


def test_a_revoked_source_stops_answering_on_the_next_request_and_restores() -> None:
    import asyncio

    c = client()
    first = ask(c, "rev00001-aaaa")
    assert first["decision"] == "ANSWER" and first["citations"] is not None
    sources = c.app.state.assistant.knowledge.answers["KB-001"].source_ids  # type: ignore[attr-defined]
    for s in sources:
        asyncio.run(revocations(c).revoke("source", s))
    pulled = ask(c, "rev00002-aaaa")
    assert pulled["decision"] == "ABSTAIN", (
        "the warm index and the loaded snapshot did not keep serving it"
    )
    assert not [x for x in pulled["citations"] if x["source_id"] in sources]  # type: ignore[attr-defined,union-attr]
    for s in sources:
        asyncio.run(revocations(c).restore("source", s))
    assert ask(c, "rev00003-aaaa")["decision"] == "ANSWER"
    c.__exit__(None, None, None)


def test_a_revoked_link_is_not_offered_and_does_not_redirect() -> None:
    import asyncio

    c = client()
    assert c.get(f"{P}/out/OVERVIEW", follow_redirects=False).status_code == 302
    assert any(x["link_id"] == "OVERVIEW" for x in c.get(f"{P}/bootstrap").json()["static_help"])
    asyncio.run(revocations(c).revoke("link", "OVERVIEW"))
    assert c.get(f"{P}/out/OVERVIEW", follow_redirects=False).status_code == 404, (
        "checked at click time"
    )
    assert not any(
        x["link_id"] == "OVERVIEW" for x in c.get(f"{P}/bootstrap").json()["static_help"]
    )
    pkt = ask(c, "rev00004-aaaa")
    assert not [a for a in pkt["actions"] if a.get("link_id") == "OVERVIEW"]  # type: ignore[attr-defined,union-attr]
    c.__exit__(None, None, None)
