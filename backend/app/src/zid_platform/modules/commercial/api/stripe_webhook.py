"""Stripe's webhook endpoint (CR-040).

    POST /v1/provider-events/stripe     signed events from the payment processor

Not a person's request: there is no session. The only credential is Stripe's signature over the raw body, so the body is
read once as bytes, checked, and only then parsed. A bad signature is a 400 and nothing is read; a database failure is a
503 so Stripe redelivers; a repeated event is a 200 that changes nothing."""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Request
from zid_errors import ZidError

from zid_platform.api_auth import request_engine
from zid_platform.modules.commercial.application import stripe_webhook_service
from zid_platform.modules.commercial.domain import stripe_webhook as hook

router = APIRouter()


@router.post("/v1/provider-events/stripe")
async def stripe_webhook(request: Request) -> dict[str, Any]:
    secrets: list[str] = list(getattr(request.app.state, "stripe_webhook_secrets", None) or [])
    if not secrets:
        raise ZidError(
            "WEBHOOK_NOT_CONFIGURED", "Payment events are not set up here.", http_status=404
        )
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > hook.MAX_BODY_BYTES:
        raise ZidError("EVENT_TOO_LARGE", "The event is too large.", http_status=413)
    raw = await request.body()
    if len(raw) > hook.MAX_BODY_BYTES:
        raise ZidError("EVENT_TOO_LARGE", "The event is too large.", http_status=413)
    try:
        hook.verify_signature(
            raw, request.headers.get("stripe-signature"), secrets, now=time.time()
        )
        event = hook.parse_event(raw)
    except hook.WebhookError as e:
        raise ZidError(e.code, "The event could not be verified.", http_status=400) from e
    try:
        outcome = await stripe_webhook_service.process(request_engine(request), event)
    except ZidError:
        raise
    except Exception as e:
        raise ZidError(
            "WEBHOOK_TEMPORARILY_UNAVAILABLE",
            "The event could not be recorded; try again.",
            http_status=503,
            retryable=True,
            retry_after_ms=2000,
        ) from e
    return {"received": True, "outcome": outcome}
