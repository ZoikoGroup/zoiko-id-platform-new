"""Claude provider through the official Anthropic SDK (ADR-05). Server-side only; the key comes from a file, never inline.

Deliberate choices, all from the platform rules in ZI-CHAT-ADR-006:
- No tools, no thinking budget, no sampling parameters, no prefill (unsupported on this model family and unnecessary).
- JSON-schema constrained output (`output_config.format`), then the server verifier still checks everything.
- No server-side fallback to another model: a refusal or outage fails closed to ABSTAIN / static help, so visitor text is never
  rerouted to a provider or region that Privacy has not approved (ADR-10, OD-010).
- Complete responses only; no streaming of unverified text (ADR-05).
"""

from __future__ import annotations

import anthropic
from anthropic.types import OutputConfigParam

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


class ClaudeProvider:
    name = "claude"

    def __init__(
        self, settings: AssistantSettings, client: anthropic.AsyncAnthropic | None = None
    ) -> None:
        self._model = settings.model_name
        self._output_config: OutputConfigParam = {
            "effort": settings.model_effort,  # type: ignore[typeddict-item]  # validated by the API: low | medium | high
            "format": {"type": "json_schema", "schema": RESPONSE_SCHEMA},
        }
        if client is not None:
            self._client = client
            return
        if settings.model_api_key_file is None:
            raise ModelUnavailable(
                "no provider key file configured (ZID_ASSISTANT_MODEL_API_KEY_FILE)"
            )
        key = settings.model_api_key_file.read_text(encoding="utf-8").strip()
        if not key:
            raise ModelUnavailable("the provider key file is empty")
        # max_retries=1: the gateway owns retries and breakers; the SDK must not multiply a slow provider's latency.
        self._client = anthropic.AsyncAnthropic(api_key=key, max_retries=1)

    async def draft(self, request: DraftRequest) -> DraftResult:
        try:
            response = await self._client.messages.create(
                model=self._model,
                max_tokens=request.max_output_tokens,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": render_user_block(request)}],
                output_config=self._output_config,
                timeout=request.timeout_seconds,
            )
        except anthropic.APITimeoutError as e:
            raise ModelUnavailable("timeout") from e
        except anthropic.RateLimitError as e:
            raise ModelUnavailable("rate_limited") from e
        except anthropic.AuthenticationError as e:
            raise ModelUnavailable("auth") from e
        except anthropic.APIConnectionError as e:
            raise ModelUnavailable("connection") from e
        except anthropic.APIStatusError as e:
            raise ModelUnavailable(f"status_{e.status_code}") from e
        if response.stop_reason == "refusal":
            raise ModelRefused("refusal")
        if response.stop_reason == "max_tokens":
            raise ModelUnavailable("max_tokens")
        text = next((b.text for b in response.content if b.type == "text"), "")
        usage = response.usage
        return DraftResult(
            raw_text=text,
            provider=self.name,
            model=self._model,
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
        )
