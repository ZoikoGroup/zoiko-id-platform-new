"""Groq (OpenAI-compatible) and Gemini providers over plain HTTPS (httpx). Server-side only; keys come from files, never inline.

Same rules as the Claude provider: no tools, no streaming, no fallback to another vendor, complete responses only. The output is untrusted
JSON text; the server verifier checks everything. A failure maps to ModelUnavailable / ModelRefused so the visitor gets static help.
"""

from __future__ import annotations

import httpx

from zid_assistant.settings import AssistantSettings

from .base import (
    RESPONSE_SCHEMA,
    SYSTEM_PROMPT,
    DraftRequest,
    DraftResult,
    ModelRefused,
    ModelUnavailable,
    render_user_block,
)

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


def _read_key(settings: AssistantSettings) -> str:
    if settings.model_api_key_file is None:
        raise ModelUnavailable("no provider key file configured (ZID_ASSISTANT_MODEL_API_KEY_FILE)")
    key = settings.model_api_key_file.read_text(encoding="utf-8").strip()
    if not key:
        raise ModelUnavailable("the provider key file is empty")
    return key


def _status_error(status: int) -> ModelUnavailable:
    if status in (401, 403):
        return ModelUnavailable("auth")
    if status == 429:
        return ModelUnavailable("rate_limited")
    return ModelUnavailable(f"status_{status}")


async def _post(
    client: httpx.AsyncClient | None,
    url: str,
    headers: dict[str, str],
    body: dict[str, object],
    timeout: float,
) -> dict[str, object]:
    try:
        if client is not None:
            r = await client.post(url, headers=headers, json=body, timeout=timeout)
        else:
            async with httpx.AsyncClient() as c:
                r = await c.post(url, headers=headers, json=body, timeout=timeout)
    except httpx.TimeoutException as e:
        raise ModelUnavailable("timeout") from e
    except httpx.HTTPError as e:
        raise ModelUnavailable("connection") from e
    if r.status_code != 200:
        raise _status_error(r.status_code)
    try:
        data = r.json()
    except ValueError as e:
        raise ModelUnavailable("bad_response") from e
    if not isinstance(data, dict):
        raise ModelUnavailable("bad_response")
    return data


class GroqProvider:
    name = "groq"

    def __init__(
        self, settings: AssistantSettings, client: httpx.AsyncClient | None = None
    ) -> None:
        self._model = settings.model_name
        self._key = _read_key(settings)
        self._client = client

    async def draft(self, request: DraftRequest) -> DraftResult:
        body: dict[str, object] = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": render_user_block(request)},
            ],
            "temperature": 0,
            "max_tokens": request.max_output_tokens,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "draft", "strict": True, "schema": RESPONSE_SCHEMA},
            },
        }
        data = await _post(
            self._client,
            GROQ_URL,
            {"Authorization": f"Bearer {self._key}"},
            body,
            request.timeout_seconds,
        )
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise ModelUnavailable("bad_response")
        choice = choices[0]
        if choice.get("finish_reason") == "length":
            raise ModelUnavailable("max_tokens")
        message = choice.get("message")
        text = message.get("content") if isinstance(message, dict) else None
        if not isinstance(text, str):
            raise ModelUnavailable("bad_response")
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        assert isinstance(usage, dict)
        return DraftResult(
            raw_text=text,
            provider=self.name,
            model=self._model,
            input_tokens=int(usage.get("prompt_tokens", 0) or 0),
            output_tokens=int(usage.get("completion_tokens", 0) or 0),
        )


class GeminiProvider:
    name = "gemini"

    def __init__(
        self, settings: AssistantSettings, client: httpx.AsyncClient | None = None
    ) -> None:
        self._model = settings.model_name
        self._key = _read_key(settings)
        self._client = client

    async def draft(self, request: DraftRequest) -> DraftResult:
        schema = {
            k: v for k, v in RESPONSE_SCHEMA.items() if k != "additionalProperties"
        }  # not accepted by Gemini's schema subset
        body: dict[str, object] = {
            "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
            "contents": [{"role": "user", "parts": [{"text": render_user_block(request)}]}],
            "generationConfig": {
                "temperature": 0,
                "maxOutputTokens": request.max_output_tokens,
                "responseMimeType": "application/json",
                "responseSchema": _gemini_schema(schema),
                "thinkingConfig": {"thinkingBudget": 0},
            },
        }
        data = await _post(
            self._client,
            GEMINI_URL.format(model=self._model),
            {"x-goog-api-key": self._key},
            body,
            request.timeout_seconds,
        )
        feedback = data.get("promptFeedback")
        if isinstance(feedback, dict) and feedback.get("blockReason"):
            raise ModelRefused("refusal")
        candidates = data.get("candidates")
        if (
            not isinstance(candidates, list)
            or not candidates
            or not isinstance(candidates[0], dict)
        ):
            raise ModelUnavailable("bad_response")
        cand = candidates[0]
        reason = cand.get("finishReason")
        if reason in ("SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "RECITATION"):
            raise ModelRefused("refusal")
        if reason == "MAX_TOKENS":
            raise ModelUnavailable("max_tokens")
        content = cand.get("content")
        parts = content.get("parts") if isinstance(content, dict) else None
        text = ""
        if isinstance(parts, list):
            text = "".join(
                p.get("text", "")
                for p in parts
                if isinstance(p, dict) and isinstance(p.get("text"), str)
            )
        if not text:
            raise ModelUnavailable("bad_response")
        usage = data.get("usageMetadata") if isinstance(data.get("usageMetadata"), dict) else {}
        assert isinstance(usage, dict)
        return DraftResult(
            raw_text=text,
            provider=self.name,
            model=self._model,
            input_tokens=int(usage.get("promptTokenCount", 0) or 0),
            output_tokens=int(usage.get("candidatesTokenCount", 0) or 0),
        )


def _gemini_schema(schema: dict[str, object]) -> dict[str, object]:
    """Gemini's OpenAPI-subset schema wants upper-case type names."""
    out: dict[str, object] = {}
    for k, v in schema.items():
        if k == "type" and isinstance(v, str):
            out[k] = v.upper()
        elif isinstance(v, dict):
            out[k] = _gemini_schema(v)
        else:
            out[k] = v
    return out
