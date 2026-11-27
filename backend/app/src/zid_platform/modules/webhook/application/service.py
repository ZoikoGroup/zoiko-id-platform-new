"""Webhook endpoint administration (ZID-EVENTS-001 §15-17, §23; API-001 §17).

    register   validate URL + resolved addresses + subscribed types -> PENDING_VERIFICATION
    verify     signed challenge; only an echo activates the endpoint (ACTIVE)
    pause      ACTIVE -> PAUSED (deliveries wait); resume PAUSED -> ACTIVE
    retire     any live status -> RETIRED (terminal; queued deliveries are never sent)
    replay     a FAILED/EXHAUSTED delivery gets a new attempt row with the original event id
Each change emits its catalogued webhook event and an audit record; payloads carry ids only.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncConnection
from zid_context import uuid7
from zid_outbox import emit

from zid_platform.modules.evidence.ports import AuditFacts, EvidenceClass, record_audit
from zid_platform.modules.webhook.domain.webhooks import WebhookError, check_event_types
from zid_platform.modules.webhook.infrastructure import delivery, endpoint_store
from zid_platform.modules.webhook.infrastructure.delivery import Resolver, Signer

LIVE = ("PENDING_VERIFICATION", "ACTIVE", "PAUSED", "DISABLED")


async def _record(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    event: str | None,
    action: str,
    target: str,
    target_ref: str,
    actor: str,
    now: datetime,
    data: dict[str, Any],
    evidence: EvidenceClass = EvidenceClass.E2,
) -> None:
    event_id = None
    if event is not None:
        event_id = await emit(
            conn,
            name=event,
            tenant_id=tenant_id,
            aggregate_type="webhook_change",
            aggregate_id=str(uuid7()),  # each change is its own fact; the ids are in the data
            aggregate_version=1,
            data=data,
            classification="INTERNAL",
        )
    await record_audit(
        conn,
        tenant_id,
        AuditFacts(
            event_type=event or f"webhook.{target}.{action}",
            actor_type="HUMAN",
            action=action,
            target_type=f"webhook_{target}",
            target_ref=target_ref,
            outcome="SUCCESS",
            evidence_class=evidence,
            occurred_at=now,
            actor_principal_id=actor,
            source_service="webhook",
            source_event_id=event_id,
            details={k: v for k, v in data.items() if isinstance(v, (str, int, bool))},
        ),
    )


async def register(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    url: str,
    event_types: list[str],
    max_inflight: int,
    signer_kid: str,
    actor: str,
    resolve: Resolver,
    now: datetime,
    allow_http: bool = False,
) -> dict[str, Any]:
    types = check_event_types(event_types)
    endpoint_id = await delivery.register(
        conn,
        tenant_id=tenant_id,
        url=url,
        event_types=types,
        key_id=signer_kid,
        created_by=actor,
        resolve=resolve,
        allow_http=allow_http,
        max_inflight=max_inflight,
    )
    await _record(
        conn,
        tenant_id,
        event="webhook.endpoint.registered",
        action="register",
        target="endpoint",
        target_ref=endpoint_id,
        actor=actor,
        now=now,
        data={"endpoint_id": endpoint_id, "event_type_count": len(types)},
    )
    return await endpoint_store.load_endpoint(conn, endpoint_id)


def explain_failure(reason: str) -> str:
    """What the address did when it was tested, and what to change, in plain words. The test sends one signed message and the address must answer 200 with the same challenge token it was sent."""
    kind, _, detail = reason.partition(":")
    if kind == "REDIRECT":
        return f"The address forwarded us somewhere else (HTTP {detail}). It must answer the test itself, directly, without redirecting to another page or to a sign-in."
    if kind == "HTTP":
        hint = (
            " That page does not exist at this address: check the path."
            if detail == "404"
            else " It must answer 200."
        )
        return f"The address answered HTTP {detail}.{hint}"
    if kind == "NOT_JSON":
        return 'The address answered 200 but not with JSON. Reply with {"challenge": "<the token you received>"}.'
    if kind == "WRONG_CHALLENGE":
        return "The address answered 200, but did not send back the same challenge token it received. Reply with the exact token."
    if kind == "TIMEOUT":
        return "The address did not answer in time. It has to reply within a few seconds."
    if kind == "UNREACHABLE":
        return "We could not connect to the address. Check that the server is running and reachable from the internet."
    return (
        "The address could not be tested. Check that it is correct and that the server is running."
    )


async def verify(
    conn: AsyncConnection,
    tenant_id: str,
    endpoint_id: str,
    *,
    client: httpx.AsyncClient,
    resolve: Resolver,
    sign: Signer,
    actor: str,
    now: datetime,
) -> dict[str, Any]:
    await endpoint_store.load_endpoint(conn, endpoint_id)  # 404 before 409
    if not await delivery.verify(conn, endpoint_id, client=client, resolve=resolve, sign=sign):
        raise WebhookError(
            "WEBHOOK_VERIFICATION_FAILED", explain_failure(delivery.last_reason(endpoint_id))
        )
    await _record(
        conn,
        tenant_id,
        event="webhook.endpoint.verified",
        action="verify",
        target="endpoint",
        target_ref=endpoint_id,
        actor=actor,
        now=now,
        data={"endpoint_id": endpoint_id},
    )
    return await endpoint_store.load_endpoint(conn, endpoint_id)


async def change_status(
    conn: AsyncConnection,
    tenant_id: str,
    endpoint_id: str,
    *,
    change: str,
    actor: str,
    now: datetime,
) -> dict[str, Any]:
    to, allowed, event = {
        "pause": ("PAUSED", ("ACTIVE",), "webhook.subscription.changed"),
        "resume": ("ACTIVE", ("PAUSED",), "webhook.subscription.changed"),
        "retire": ("RETIRED", LIVE, "webhook.endpoint.disabled"),
    }[change]
    endpoint = await endpoint_store.set_status(conn, endpoint_id, to=to, allowed_from=allowed)
    await _record(
        conn,
        tenant_id,
        event=event,
        action=change,
        target="endpoint",
        target_ref=endpoint_id,
        actor=actor,
        now=now,
        data={"endpoint_id": endpoint_id, "status": to},
    )
    return endpoint


async def replay(
    conn: AsyncConnection, tenant_id: str, delivery_id: str, *, actor: str, now: datetime
) -> dict[str, Any]:
    original = await endpoint_store.load_delivery(conn, delivery_id)
    endpoint = await endpoint_store.load_endpoint(conn, original["endpoint_id"])
    if endpoint["status"] != "ACTIVE":
        raise WebhookError("WEBHOOK_ENDPOINT_PAUSED", "the endpoint must be ACTIVE to replay")
    new_id = await delivery.replay(conn, delivery_id)
    await _record(
        conn,
        tenant_id,
        event=None,
        action="replay",
        target="delivery",
        target_ref=new_id,
        actor=actor,
        now=now,
        data={"delivery_id": new_id, "replay_of": delivery_id, "event_id": original["event_id"]},
    )
    return await endpoint_store.load_delivery(conn, new_id)
