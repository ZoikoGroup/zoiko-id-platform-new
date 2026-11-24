import asyncio
import json
from pathlib import Path

import httpx
import pytest
from zid_assistant.infrastructure.model.base import (
    DraftRequest,
    EvidenceItem,
    ModelRefused,
    ModelUnavailable,
)
from zid_assistant.infrastructure.model.gateway import build_gateway
from zid_assistant.infrastructure.model.http_providers import GeminiProvider, GroqProvider
from zid_assistant.settings import AssistantSettings

REQ = DraftRequest("q", (EvidenceItem("PUB-1", "s1", "text"),), 300, 5.0)
OUT = json.dumps({"decision": "ANSWER", "answer_text": "a", "span_refs": ["PUB-1#s1"]})


def _settings(tmp_path: Path, provider: str = "groq") -> AssistantSettings:
    key = tmp_path / "k.key"
    key.write_text("secret-key\n", encoding="utf-8")
    return AssistantSettings(
        _env_file=None, model_provider=provider, model_name="m", model_api_key_file=key
    )  # type: ignore[call-arg]


def _client(handler) -> httpx.AsyncClient:  # type: ignore[no-untyped-def]
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_groq_sends_key_in_header_and_schema_and_parses(tmp_path: Path) -> None:
    seen: dict[str, object] = {}

    def handler(r: httpx.Request) -> httpx.Response:
        seen["auth"] = r.headers["authorization"]
        seen["body"] = json.loads(r.content)
        return httpx.Response(
            200,
            json={
                "choices": [{"finish_reason": "stop", "message": {"content": OUT}}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 4},
            },
        )

    res = asyncio.run(GroqProvider(_settings(tmp_path), _client(handler)).draft(REQ))
    assert (
        res.raw_text == OUT
        and res.provider == "groq"
        and (res.input_tokens, res.output_tokens) == (3, 4)
    )
    assert seen["auth"] == "Bearer secret-key"
    body = seen["body"]
    assert (
        isinstance(body, dict)
        and "tools" not in body
        and body["response_format"]["type"] == "json_schema"
    )  # type: ignore[index]


def test_gemini_key_not_in_url_and_parses(tmp_path: Path) -> None:
    seen: dict[str, object] = {}

    def handler(r: httpx.Request) -> httpx.Response:
        seen["url"], seen["hdr"] = str(r.url), r.headers["x-goog-api-key"]
        return httpx.Response(
            200,
            json={"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": OUT}]}}]},
        )

    res = asyncio.run(GeminiProvider(_settings(tmp_path, "gemini"), _client(handler)).draft(REQ))
    assert (
        res.raw_text == OUT and "secret-key" not in str(seen["url"]) and seen["hdr"] == "secret-key"
    )


@pytest.mark.parametrize(
    ("status", "reason"), [(401, "auth"), (429, "rate_limited"), (500, "status_500")]
)
def test_http_errors_become_unavailable(tmp_path: Path, status: int, reason: str) -> None:
    with pytest.raises(ModelUnavailable, match=reason):
        asyncio.run(
            GroqProvider(
                _settings(tmp_path), _client(lambda r: httpx.Response(status, json={}))
            ).draft(REQ)
        )


def test_gemini_block_is_a_refusal_and_truncation_is_unavailable(tmp_path: Path) -> None:
    s = _settings(tmp_path, "gemini")
    blocked = _client(
        lambda r: httpx.Response(200, json={"promptFeedback": {"blockReason": "SAFETY"}})
    )
    with pytest.raises(ModelRefused):
        asyncio.run(GeminiProvider(s, blocked).draft(REQ))
    cut = _client(
        lambda r: httpx.Response(
            200,
            json={
                "candidates": [
                    {"finishReason": "MAX_TOKENS", "content": {"parts": [{"text": "{"}]}}
                ]
            },
        )
    )
    with pytest.raises(ModelUnavailable, match="max_tokens"):
        asyncio.run(GeminiProvider(s, cut).draft(REQ))


def test_gateway_builds_these_providers_and_fails_closed_without_a_key(tmp_path: Path) -> None:
    gw = build_gateway(_settings(tmp_path, "gemini"))
    assert gw is not None and gw.provider_name == "gemini"
    no_key = AssistantSettings(_env_file=None, model_provider="groq", model_api_key_file=None)  # type: ignore[call-arg]
    assert build_gateway(no_key) is None
