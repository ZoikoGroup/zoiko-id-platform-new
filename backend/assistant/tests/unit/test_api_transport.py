"""Transport guards of the public API (ENG-003 section 04 'transport constraints'; ADR-06, ADR-07): origin, content type, size, cookie, errors."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from assistant_helpers import H, P, make_settings
from fastapi.testclient import TestClient
from zid_assistant.application.snapshot import Knowledge
from zid_assistant.infrastructure.sessions import MemorySessionStore
from zid_assistant.infrastructure.store import MemoryStore
from zid_assistant.main import create_app
from zid_assistant.settings import AssistantSettings

MSG = {"client_message_id": "tttttttt-0001", "text": "What is Zoiko iD?"}


def new_client(**kw: object) -> TestClient:
    app = create_app(
        make_settings(**kw), store=MemoryStore(), sessions=MemorySessionStore(1800, 7200)
    )
    return TestClient(app, base_url="http://localhost:8210")


def error_code(r: object) -> str:
    return r.json()["error"]["code"]  # type: ignore[attr-defined,no-any-return]


def test_every_error_has_the_public_shape_and_never_echoes_input(client: TestClient) -> None:
    r = client.post(
        f"{P}/messages",
        headers=H,
        json={"client_message_id": "ECHO-CANARY-1", "text": "x", "tenant_id": "ECHO-CANARY-2"},
    )
    assert r.status_code == 401  # no session yet
    body = r.json()
    assert set(body) == {"error"} and set(body["error"]) >= {
        "code",
        "message",
        "correlation_id",
        "retry_after_s",
    }
    assert "ECHO-CANARY" not in r.text
    assert r.headers["cache-control"] == "no-store"


def test_state_changing_calls_need_an_allowlisted_origin(client: TestClient) -> None:
    for headers in (
        {},
        {"origin": "http://evil.example"},
        {"origin": "null"},
        {"origin": "https://www.zoikoid.example.evil.example"},
    ):
        assert client.post(f"{P}/sessions", headers=headers).status_code == 403, headers
        assert client.delete(f"{P}/sessions", headers=headers).status_code == 403, headers
        assert client.post(f"{P}/messages", headers=headers, json=MSG).status_code == 403, headers
        assert (
            client.post(
                f"{P}/feedback",
                headers=headers,
                json={"response_id": "rsp_" + "a" * 32, "rating": "USEFUL"},
            ).status_code
            == 403
        )
    assert client.post(f"{P}/sessions", headers=H).status_code == 201


def test_only_json_is_accepted(session_client: TestClient) -> None:
    for ctype in (
        "text/plain",
        "application/x-www-form-urlencoded",
        "multipart/form-data; boundary=x",
        "",
    ):
        r = session_client.post(
            f"{P}/messages", headers={**H, "content-type": ctype}, content=json.dumps(MSG)
        )
        assert r.status_code == 415 and error_code(r) == "UNSUPPORTED_MEDIA_TYPE", ctype
    assert (
        session_client.post(
            f"{P}/messages",
            headers={**H, "content-type": "application/json; charset=utf-8"},
            content=json.dumps(MSG),
        ).status_code
        == 200
    )


def test_malformed_and_oversized_bodies_are_rejected(session_client: TestClient) -> None:
    h = {**H, "content-type": "application/json"}
    assert session_client.post(f"{P}/messages", headers=h, content="{not json").status_code == 400
    assert session_client.post(f"{P}/messages", headers=h, content="[1,2,3]").status_code == 400
    big = json.dumps({**MSG, "text": "a" * 20_000})
    r = session_client.post(f"{P}/messages", headers=h, content=big)
    assert r.status_code == 413 and error_code(r) == "PAYLOAD_TOO_LARGE"


@pytest.mark.parametrize("text", ["", "   ", "a" * 1001])
def test_message_text_is_bounded(session_client: TestClient, text: str) -> None:
    r = session_client.post(f"{P}/messages", headers=H, json={**MSG, "text": text})
    assert r.status_code == 400 and error_code(r) == "INVALID_REQUEST"


@pytest.mark.parametrize(
    "extra",
    [
        {"client_message_id": "short"},
        {"client_message_id": "has spaces here!!"},
        {"locale": "not a locale"},
        {"intent_hint": "x" * 50},
        {"context": {"a": "b", "c": "d", "e": "f", "g": "h"}},
        {"context": {"k" * 30: "v"}},
    ],
)
def test_message_fields_are_validated(session_client: TestClient, extra: dict[str, object]) -> None:
    r = session_client.post(f"{P}/messages", headers=H, json={**MSG, **extra})
    assert r.status_code == 400, extra


def test_the_session_cookie_is_opaque_host_scoped_and_httponly(client: TestClient) -> None:
    r = client.post(f"{P}/sessions", headers=H)
    cookie = r.headers["set-cookie"]
    assert (
        cookie.startswith("zid_pa_session=")
        and "HttpOnly" in cookie
        and "SameSite=lax" in cookie
        and f"Path={P}" in cookie
    )
    assert "Max-Age=7200" in cookie and "Domain" not in cookie, "host-only, bounded"
    assert "Secure" not in cookie, "local http"
    handle = cookie.split(";")[0].split("=", 1)[1]
    assert len(handle) >= 40 and handle.replace("-", "").replace("_", "").isalnum()


def test_the_cookie_is_secure_outside_local(knowledge: Knowledge, tmp_path: Path) -> None:
    from dataclasses import replace

    from cryptography.fernet import Fernet

    key = tmp_path / "intake.key"
    key.write_text(Fernet.generate_key().decode(), encoding="utf-8")
    signed = replace(
        knowledge, snapshot=replace(knowledge.snapshot, sample=False, signed=True)
    )  # what a production process would load
    app = create_app(
        make_settings(environment="production", intake_encryption_key_file=key),
        knowledge=signed,
        store=MemoryStore(),
        sessions=MemorySessionStore(1800, 7200),
    )
    c = TestClient(app, base_url="http://localhost:8210")
    r = c.post(f"{P}/sessions", headers=H)
    assert r.status_code == 201 and "Secure" in r.headers["set-cookie"]


def test_missing_garbage_and_forgotten_sessions_are_401(client: TestClient) -> None:
    assert client.post(f"{P}/messages", headers=H, json=MSG).status_code == 401
    client.cookies.set("zid_pa_session", "garbage", path=P)
    assert client.post(f"{P}/messages", headers=H, json=MSG).status_code == 401
    client.cookies.clear()
    assert client.post(f"{P}/sessions", headers=H).status_code == 201
    assert client.post(f"{P}/messages", headers=H, json=MSG).status_code == 200
    assert client.delete(f"{P}/sessions", headers=H).status_code == 204
    r = client.post(f"{P}/messages", headers=H, json={**MSG, "client_message_id": "tttttttt-0002"})
    assert r.status_code == 401 and error_code(r) == "INVALID_GUEST_SESSION"


def test_session_creation_is_rate_limited_per_client_address() -> None:
    c = new_client(rate_limit_sessions=2)
    codes = [
        c.post(f"{P}/sessions", headers={**H, "x-forwarded-for": "203.0.113.5"}).status_code
        for _ in range(4)
    ]
    assert codes == [201, 201, 429, 429]
    assert (
        c.post(f"{P}/sessions", headers={**H, "x-forwarded-for": "203.0.113.6"}).status_code == 201
    ), "another address is unaffected"


def test_replay_returns_the_same_packet_and_a_different_text_conflicts(
    session_client: TestClient,
) -> None:
    a = session_client.post(f"{P}/messages", headers=H, json=MSG).json()
    b = session_client.post(f"{P}/messages", headers=H, json=MSG).json()
    assert a == b
    r = session_client.post(f"{P}/messages", headers=H, json={**MSG, "text": "Something different"})
    assert r.status_code == 409 and error_code(r) == "IDEMPOTENCY_CONFLICT"


def test_feedback_needs_a_real_response_and_counts_once(
    session_client: TestClient, store: MemoryStore
) -> None:
    rid = session_client.post(f"{P}/messages", headers=H, json=MSG).json()["response_id"]
    for bad in (
        {"response_id": "rsp_" + "f" * 32, "rating": "USEFUL"},
        {"response_id": rid, "rating": "AMAZING"},
        {"response_id": "not-an-id", "rating": "USEFUL"},
        {"response_id": rid, "rating": "USEFUL", "comment": "free text"},
    ):
        assert session_client.post(f"{P}/feedback", headers=H, json=bad).status_code == 400, bad
    assert (
        session_client.post(
            f"{P}/feedback", headers=H, json={"response_id": rid, "rating": "USEFUL"}
        ).status_code
        == 204
    )
    assert (
        session_client.post(
            f"{P}/feedback", headers=H, json={"response_id": rid, "rating": "NOT_USEFUL"}
        ).status_code
        == 204
    )


def test_bootstrap_is_cacheable_and_reports_the_disabled_state() -> None:
    c = new_client(enabled=False)
    r = c.get(f"{P}/bootstrap")
    etag = r.headers["etag"]
    assert r.json()["enabled"] is False and r.json()["starters"] == [] and r.json()["static_help"]
    assert c.get(f"{P}/bootstrap", headers={"if-none-match": etag}).status_code == 304
    assert c.post(f"{P}/sessions", headers=H).status_code == 503
    on = new_client()
    body = on.get(f"{P}/bootstrap").json()
    assert body["enabled"] is True and len(body["starters"]) == 7
    tiles = [s for s in body["starters"] if s.get("icon")]
    assert len(tiles) == 4 and all(s["description"] for s in tiles)
    assert all(f["link_id"] in {"TALK-TO-EXPERT", "PRIVACY-NOTICE"} for f in body["footer_links"])


def test_unapproved_locales_get_the_approved_default(session_client: TestClient) -> None:
    r = session_client.post(f"{P}/messages", headers=H, json={**MSG, "locale": "de-DE"})
    assert r.status_code == 200 and r.json()["decision"] in ("ANSWER", "ABSTAIN")


def test_the_redirect_sets_safe_headers(client: TestClient) -> None:
    r = client.get(f"{P}/out/OVERVIEW", follow_redirects=False)
    assert (
        r.status_code == 302
        and r.headers["referrer-policy"] == "no-referrer"
        and r.headers["cache-control"] == "no-store"
    )


def test_unknown_routes_and_methods_are_closed(client: TestClient) -> None:
    assert client.get(f"{P}/nope").status_code == 404
    assert client.put(f"{P}/messages", headers=H, json=MSG).status_code == 405
    assert client.get(f"{P}/messages").status_code == 405


@pytest.mark.parametrize(
    "origin",
    [
        "http://zoikoid.com",
        "https://*.zoikoid.com",
        "https://zoikoid.com/",
        "https://zoikoid.com/chat",
        "https://user@zoikoid.com",
        "https://localhost:3000",
    ],
)
def test_production_refuses_inexact_origins(origin: str) -> None:
    with pytest.raises(ValueError, match="exact https origin"):
        AssistantSettings(_env_file=None, environment="production", allowed_origins=[origin])  # type: ignore[call-arg]


def test_production_accepts_exact_https_origins() -> None:
    s = AssistantSettings(
        _env_file=None,
        environment="production",
        allowed_origins=["https://zoikoid.com", "https://www.zoikoid.com"],
    )  # type: ignore[call-arg]
    assert s.allowed_origins == ["https://zoikoid.com", "https://www.zoikoid.com"]
