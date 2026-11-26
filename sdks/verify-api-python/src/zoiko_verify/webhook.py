"""Checking a Zoiko iD webhook delivery (RFC 9421 HTTP message signature, RFC 9530 content digest).

Call `verify_webhook` with the URL your endpoint is registered at, the EXACT raw request body and the request headers, and the published signing keys (`GET /v1/webhooks/signing-keys`). It returns the event id when the
delivery is genuine and raises `WebhookError` otherwise. Deliveries are at least once and can arrive out of order or twice: deduplicate on the event id and reconcile with the decision's `decision_version`.
There is no unsigned path: a missing, stale, altered or wrongly keyed delivery is refused.
"""

from __future__ import annotations

import base64
import hashlib
import re
import time
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import urlsplit

from zoiko_verify._jwt import verify_signature

SIGNED_COMPONENTS = ("@method", "@authority", "@target-uri", "content-type", "content-digest", "x-zoiko-event-id")
MAX_SIGNATURE_AGE = 300  # seconds a signature's `created` is accepted for


class WebhookError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def content_digest(body: bytes) -> str:
    return "sha-256=:" + base64.b64encode(hashlib.sha256(body).digest()).decode() + ":"


def verify_webhook(
    *,
    url: str,
    body: bytes,
    headers: Mapping[str, str],
    keys: Sequence[Mapping[str, Any]],
    now: float | None = None,
    max_age: int = MAX_SIGNATURE_AGE,
) -> str:
    h = {k.lower(): v for k, v in headers.items()}
    signature_input, signature = h.get("signature-input"), h.get("signature")
    if not signature_input or not signature:
        raise WebhookError("WEBHOOK_SIGNATURE_MISSING")
    match = re.fullmatch(r'zid=(\(.*\));created=(\d+);keyid="([^"]+)"', signature_input)
    if match is None or not signature.startswith("zid=:") or not signature.endswith(":"):
        raise WebhookError("WEBHOOK_SIGNATURE_MALFORMED")
    components, created, key_id = match.group(1), int(match.group(2)), match.group(3)
    expected = "(" + " ".join(f'"{c}"' for c in SIGNED_COMPONENTS) + ")"
    if components != expected:
        raise WebhookError(
            "WEBHOOK_SIGNATURE_MALFORMED"
        )  # a signature that covers less than the full set is not accepted
    if abs((time.time() if now is None else now) - created) > max_age:
        raise WebhookError("WEBHOOK_SIGNATURE_STALE")
    if h.get("content-digest") != content_digest(body):
        raise WebhookError("WEBHOOK_DIGEST_MISMATCH")
    jwk = next((dict(k) for k in keys if k.get("kid") == key_id), None)
    if jwk is None:
        raise WebhookError("WEBHOOK_KEY_UNKNOWN")  # a retired key is gone: no fallback
    values = {
        "@method": "POST",
        "@authority": urlsplit(url).netloc.lower(),
        "@target-uri": url,
        "content-type": h.get("content-type", ""),
        "content-digest": h["content-digest"],
        "x-zoiko-event-id": h.get("x-zoiko-event-id", ""),
    }
    base = (
        "\n".join(f'"{c}": {values[c]}' for c in SIGNED_COMPONENTS)
        + f'\n"@signature-params": {components};created={created};keyid="{key_id}"'
    )
    try:
        verify_signature(jwk, base.encode(), base64.b64decode(signature[len("zid=:") : -1]))
    except Exception as e:
        raise WebhookError("WEBHOOK_SIGNATURE_INVALID") from e
    return values["x-zoiko-event-id"]
