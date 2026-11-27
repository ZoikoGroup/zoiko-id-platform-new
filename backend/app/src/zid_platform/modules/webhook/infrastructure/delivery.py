"""Webhook registration, routing and delivery (ZID-EVENTS-001 §14-20).

- `register`: validates the URL and its resolved addresses, stores the endpoint PENDING_VERIFICATION.
- `verify`: sends a signed challenge; only an echo of the challenge activates the endpoint.
- `route`: turns one integration event into a delivery snapshot for every ACTIVE endpoint subscribed to it
  (called from the event consumer, idempotent per (endpoint, event)).
- `deliver_due`: claims due deliveries with SKIP LOCKED, re-resolves DNS and re-checks addresses before
  each send, never follows redirects, respects per-endpoint max_inflight, and applies the §19 policy.
The HTTP client and DNS resolver are injected, so egress can run through the SSRF-safe gateway.
"""

from __future__ import annotations

import json
import secrets
import uuid
from collections.abc import Awaitable, Callable, Iterable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection
from zid_context import uuid7
from zid_db import in_tenant
from zid_outbox import CloudEvent, emit

from zid_platform.modules.webhook.domain.webhooks import (
    DeliveryStatus,
    NextStep,
    Outcome,
    WebhookError,
    check_resolution,
    classify,
    content_digest,
    next_step,
    sign_request,
    validate_url,
)

Resolver = Callable[[str], Awaitable[list[str]]]
Signer = Callable[[bytes], bytes]


async def register(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    url: str,
    event_types: Iterable[str],
    key_id: str,
    created_by: str,
    resolve: Resolver,
    allow_http: bool = False,
    max_inflight: int = 4,
) -> str:
    clean = validate_url(url, allow_http=allow_http)
    check_resolution(await resolve(httpx.URL(clean).host))
    endpoint_id = str(uuid7())
    await conn.execute(
        text(
            "INSERT INTO events.webhook_endpoint (endpoint_id, tenant_id, url, event_types, status, signing_key_id, "
            "verification_token, max_inflight, created_by) VALUES (:id, :t, :u, :types, 'PENDING_VERIFICATION', "
            ":k, :tok, :mi, :by)"
        ),
        {
            "id": uuid.UUID(endpoint_id),
            "t": uuid.UUID(tenant_id),
            "u": clean,
            "types": sorted(set(event_types)),
            "k": key_id,
            "tok": secrets.token_urlsafe(24),
            "mi": max_inflight,
            "by": uuid.UUID(created_by),
        },
    )
    return endpoint_id


async def verify(
    conn: AsyncConnection,
    endpoint_id: str,
    *,
    client: httpx.AsyncClient,
    resolve: Resolver,
    sign: Signer,
) -> bool:
    row = (
        await conn.execute(
            text(
                "SELECT url, signing_key_id, verification_token FROM events.webhook_endpoint "
                "WHERE endpoint_id = :e AND status = 'PENDING_VERIFICATION' FOR UPDATE"
            ),
            {"e": uuid.UUID(endpoint_id)},
        )
    ).first()
    if row is None:
        raise WebhookError("WEBHOOK_ENDPOINT_NOT_PENDING")
    check_resolution(await resolve(httpx.URL(row.url).host))
    body = json.dumps(
        {"type": "com.zoiko.id.webhook.verification.v1", "challenge": row.verification_token}
    ).encode()
    signed = sign_request(
        url=row.url,
        body=body,
        event_id=f"verify-{endpoint_id}",
        delivery_id=str(uuid7()),
        key_id=row.signing_key_id,
        created=int(datetime.now(UTC).timestamp()),
        sign=sign,
    )
    reason = ""
    try:
        resp = await client.post(
            row.url, content=body, headers=signed.headers, follow_redirects=False
        )
        reason = answer_reason(resp, row.verification_token)
    except httpx.TimeoutException:
        reason = "TIMEOUT"
    except httpx.ConnectError:
        reason = "UNREACHABLE"
    except (httpx.HTTPError, ValueError):
        reason = "ERROR"
    ok = reason == ""
    _LAST_REASON[endpoint_id] = reason
    if ok:
        await conn.execute(
            text(
                "UPDATE events.webhook_endpoint SET status = 'ACTIVE', verification_token = NULL, "
                "updated_at = now() WHERE endpoint_id = :e"
            ),
            {"e": uuid.UUID(endpoint_id)},
        )
    return ok


# Why the last verification of each endpoint failed, as a short code ("" when it passed), so the person is told what the address answered and not only that it failed. Process-local and
# only ever read right after the verify call that wrote it; nothing here is stored or sent anywhere.
_LAST_REASON: dict[str, str] = {}


def answer_reason(resp: Any, token: str) -> str:
    """What was wrong with an endpoint's answer to the challenge: "" when it was right, otherwise a code the service turns into a sentence."""
    code = resp.status_code
    if code in (301, 302, 303, 307, 308):
        return f"REDIRECT:{code}"
    if code != 200:
        return f"HTTP:{code}"
    try:
        answered = resp.json()
    except ValueError:
        return "NOT_JSON"
    if not isinstance(answered, dict) or answered.get("challenge") != token:
        return "WRONG_CHALLENGE"
    return ""


def last_reason(endpoint_id: str) -> str:
    return _LAST_REASON.pop(endpoint_id, "")


async def route(conn: AsyncConnection, event: CloudEvent) -> int:
    """Create one delivery snapshot per subscribed ACTIVE endpoint (in the event's tenant transaction)."""
    payload = json.dumps(event.to_dict(), separators=(",", ":"), default=str)
    rows = (
        await conn.execute(
            text(
                "SELECT endpoint_id FROM events.webhook_endpoint WHERE status = 'ACTIVE' "
                "AND :type = ANY(event_types)"
            ),
            {"type": event.type},
        )
    ).all()
    created = 0
    for r in rows:
        result = await conn.execute(
            text(
                "INSERT INTO events.webhook_delivery (delivery_id, tenant_id, endpoint_id, event_id, event_type, "
                "payload, payload_digest, status) VALUES (:d, :t, :e, :ev, :type, CAST(:p AS jsonb), :dig, 'QUEUED') "
                "ON CONFLICT DO NOTHING"
            ),
            {
                "d": uuid7(),
                "t": uuid.UUID(event.tenantid),
                "e": r.endpoint_id,
                "ev": uuid.UUID(event.id),
                "type": event.type,
                "p": payload,
                "dig": content_digest(payload.encode()),
            },
        )
        created += result.rowcount
    return created


def _retry_after(resp: httpx.Response | None) -> timedelta | None:
    if resp is None:
        return None
    value = resp.headers.get("retry-after", "")
    return timedelta(seconds=int(value)) if value.isdigit() else None


_OUTCOME_EVENT = {
    DeliveryStatus.DELIVERED: "webhook.delivery.succeeded",
    DeliveryStatus.RETRY_SCHEDULED: "webhook.delivery.failed",
    DeliveryStatus.FAILED: "webhook.delivery.failed",
    DeliveryStatus.EXHAUSTED: "webhook.delivery.exhausted",
}


async def _delivery_outcome(
    conn: AsyncConnection,
    r: Any,
    status: DeliveryStatus,
    attempt: int,
    code: int | None,
    now: datetime,
) -> None:
    """What one attempt came to (ZID-EVENTS-001 webhook.delivery.*; WF-02 M12): delivered means the recipient answered 2xx, never just sent.
    Ids, codes and the delay only: never the payload."""
    name = _OUTCOME_EVENT.get(status)
    if name is None:
        return
    async with in_tenant(conn, str(r.tenant_id)):
        await emit(
            conn, name=name, tenant_id=str(r.tenant_id), aggregate_type="webhook_delivery", aggregate_id=str(r.delivery_id), aggregate_version=attempt,
            data={"delivery_id": str(r.delivery_id), "event_id": str(r.event_id), "event_type": r.event_type, "attempt": attempt, "status_code": code,
                  "final": status is not DeliveryStatus.RETRY_SCHEDULED, "delay_ms": round((now - r.created_at).total_seconds() * 1000)},
        )  # fmt: skip


async def deliver_due(
    conn: AsyncConnection,
    *,
    client: httpx.AsyncClient,
    resolve: Resolver,
    sign: Signer,
    now: datetime | None = None,
    batch: int = 50,
) -> dict[str, int]:
    """One delivery pass (worker role). Returns counts by resulting status."""
    now = now or datetime.now(UTC)
    rows = (
        await conn.execute(
            text("""
                SELECT d.delivery_id, d.tenant_id, d.event_id, d.event_type, d.created_at, d.payload, d.attempts, e.endpoint_id, e.url,
                       e.signing_key_id, e.max_inflight
                FROM events.webhook_delivery d JOIN events.webhook_endpoint e USING (endpoint_id)
                WHERE d.status IN ('QUEUED', 'RETRY_SCHEDULED') AND d.next_attempt_at <= :now AND e.status = 'ACTIVE'
                ORDER BY d.next_attempt_at LIMIT :n FOR UPDATE OF d SKIP LOCKED
            """),
            {"now": now, "n": batch},
        )
    ).all()
    counts: dict[str, int] = {}
    inflight: dict[uuid.UUID, int] = {}
    for r in rows:
        if inflight.get(r.endpoint_id, 0) >= r.max_inflight:
            continue  # this endpoint's share is used up; its other deliveries wait for the next pass
        inflight[r.endpoint_id] = inflight.get(r.endpoint_id, 0) + 1
        body = json.dumps(r.payload, separators=(",", ":")).encode()
        resp: httpx.Response | None = None
        try:
            check_resolution(
                await resolve(httpx.URL(r.url).host)
            )  # DNS re-resolved on every attempt
            signed = sign_request(
                url=r.url,
                body=body,
                event_id=str(r.event_id),
                delivery_id=str(r.delivery_id),
                key_id=r.signing_key_id,
                created=int(now.timestamp()),
                sign=sign,
            )
            resp = await client.post(
                r.url, content=body, headers=signed.headers, follow_redirects=False
            )
            outcome = classify(resp.status_code)
            error = None
        except WebhookError as e:
            outcome, error = Outcome.CONTRACT, e.code  # destination now blocked: policy failure
        except httpx.HTTPError as e:
            outcome, error = Outcome.TRANSIENT, type(e).__name__
        attempts = r.attempts + 1
        step = next_step(outcome, attempt=attempts, now=now, retry_after=_retry_after(resp))
        if error == "WEBHOOK_DESTINATION_BLOCKED":
            step = NextStep(DeliveryStatus.FAILED, None)
        await conn.execute(
            text(
                "UPDATE events.webhook_delivery SET status = :s, attempts = :a, next_attempt_at = coalesce(:n, "
                "next_attempt_at), last_status_code = :code, last_error = :err, delivered_at = CASE WHEN :s = "
                "'DELIVERED' THEN :now ELSE delivered_at END WHERE delivery_id = :d"
            ),
            {
                "s": step.status.value,
                "a": attempts,
                "n": step.next_attempt_at,
                "code": resp.status_code if resp else None,
                "err": error,
                "now": now,
                "d": r.delivery_id,
            },
        )
        await _delivery_outcome(
            conn, r, step.status, attempts, resp.status_code if resp else None, now
        )
        if step.disable_endpoint:
            await conn.execute(
                text(
                    "UPDATE events.webhook_endpoint SET status = 'DISABLED', updated_at = now() "
                    "WHERE endpoint_id = :e"
                ),
                {"e": r.endpoint_id},
            )
        counts[step.status.value] = counts.get(step.status.value, 0) + 1
    return counts


async def replay(conn: AsyncConnection, delivery_id: str) -> str:
    """Operator/customer replay of a FAILED/EXHAUSTED delivery: new attempt row, same CloudEvent id."""
    new_id = uuid7()
    result = await conn.execute(
        text(
            "INSERT INTO events.webhook_delivery (delivery_id, tenant_id, endpoint_id, event_id, event_type, payload, "
            "payload_digest, status, replay_of) SELECT :n, tenant_id, endpoint_id, event_id, event_type, payload, "
            "payload_digest, 'QUEUED', delivery_id FROM events.webhook_delivery WHERE delivery_id = :d "
            "AND status IN ('FAILED', 'EXHAUSTED')"
        ),
        {"n": new_id, "d": uuid.UUID(delivery_id)},
    )
    if result.rowcount != 1:
        raise WebhookError(
            "WEBHOOK_REPLAY_NOT_ALLOWED", "only FAILED or EXHAUSTED deliveries can be replayed"
        )
    return str(new_id)
