"""Webhook register -> verify -> route -> deliver -> retry -> disable -> replay over PostgreSQL (EVENTS §14-20)."""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from conftest import ctx, engine
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy import text
from zid_db import tenant_transaction
from zid_outbox import CloudEvent
from zid_platform.modules.webhook.domain.webhooks import WebhookError
from zid_platform.modules.webhook.infrastructure.delivery import (
    deliver_due,
    register,
    replay,
    route,
    verify,
)

KEY = Ed25519PrivateKey.generate()
NOW = datetime.now(UTC)


async def public_dns(host: str) -> list[str]:
    return {"hooks.example.com": ["93.184.216.34"], "rebind.example.com": ["10.0.0.7"]}.get(
        host, []
    )


class Receiver:
    """In-process receiver: answers per a script of status codes, records what it got."""

    def __init__(self, codes: list[int]) -> None:
        self.codes, self.seen = codes, []

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body.get("type") == "com.zoiko.id.webhook.verification.v1":
            return httpx.Response(200, json={"challenge": body["challenge"]})
        self.seen.append(request)
        return httpx.Response(self.codes.pop(0) if self.codes else 200)


def event(tenant: str) -> CloudEvent:
    return CloudEvent(
        id=str(uuid.uuid4()),
        type="com.zoiko.id.mandate.revoked.v1",
        source="urn:zoiko:id:mandate",
        subject="mandate/m1",
        time=NOW,
        tenantid=tenant,
        cellid="uk-1",
        aggregatetype="mandate",
        aggregateid="m1",
        aggregateversion=4,
        data={"status": "REVOKED"},
        correlationid="c1",
    )


def test_webhook_lifecycle(rt_url: str) -> None:
    tenant, admin = str(uuid.uuid4()), str(uuid.uuid4())
    rx = Receiver([503, 200])

    async def run() -> None:
        client = httpx.AsyncClient(transport=httpx.MockTransport(rx.handler))
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            with pytest.raises(
                WebhookError
            ):  # resolves to a private address: refused at registration
                await register(
                    c,
                    tenant_id=tenant,
                    url="https://rebind.example.com/h",
                    event_types=["x"],
                    key_id="k",
                    created_by=admin,
                    resolve=public_dns,
                )
            ep = await register(
                c,
                tenant_id=tenant,
                url="https://hooks.example.com/zoiko",
                event_types=["com.zoiko.id.mandate.revoked.v1"],
                key_id="zid-whk-2026-10",
                created_by=admin,
                resolve=public_dns,
            )
            ev = event(tenant)
            assert await route(c, ev) == 0  # not verified yet: nothing is delivered
            assert await verify(c, ep, client=client, resolve=public_dns, sign=KEY.sign)
            assert (
                await route(c, ev) == 1 and await route(c, ev) == 0
            )  # one delivery per (endpoint, event)

            t0 = datetime.now(UTC) + timedelta(seconds=1)  # deliveries are queued at database now()
            first = await deliver_due(c, client=client, resolve=public_dns, sign=KEY.sign, now=t0)
            assert first == {"RETRY_SCHEDULED": 1}  # 503 -> retry with backoff
            assert (
                await deliver_due(c, client=client, resolve=public_dns, sign=KEY.sign, now=t0) == {}
            )  # not due
            later = t0 + timedelta(minutes=1)
            assert await deliver_due(
                c, client=client, resolve=public_dns, sign=KEY.sign, now=later
            ) == {"DELIVERED": 1}
            sent = rx.seen[-1]
            assert sent.headers["X-Zoiko-Event-ID"] == ev.id and "zid=" in sent.headers["Signature"]
            assert json.loads(sent.content)["id"] == ev.id  # snapshot is the CloudEvent

            # 410 Gone disables the endpoint; replay is only for FAILED/EXHAUSTED and keeps the event id
            rx.codes = [410]
            ev2 = event(tenant)
            await route(c, ev2)
            assert await deliver_due(
                c, client=client, resolve=public_dns, sign=KEY.sign, now=later
            ) == {"FAILED": 1}
            status = (
                await c.execute(
                    text("SELECT status FROM events.webhook_endpoint WHERE endpoint_id = :e"),
                    {"e": uuid.UUID(ep)},
                )
            ).scalar_one()
            assert status == "DISABLED"
            failed = (
                await c.execute(
                    text("SELECT delivery_id FROM events.webhook_delivery WHERE event_id = :e"),
                    {"e": uuid.UUID(ev2.id)},
                )
            ).scalar_one()
            new = await replay(c, str(failed))
            row = (
                await c.execute(
                    text(
                        "SELECT event_id, replay_of, status FROM events.webhook_delivery "
                        "WHERE delivery_id = :d"
                    ),
                    {"d": uuid.UUID(new)},
                )
            ).one()
            assert (str(row.event_id), row.replay_of, row.status) == (ev2.id, failed, "QUEUED")
            with pytest.raises(WebhookError):
                delivered = (
                    await c.execute(
                        text("SELECT delivery_id FROM events.webhook_delivery WHERE event_id = :e"),
                        {"e": uuid.UUID(ev.id)},
                    )
                ).scalar_one()
                await replay(c, str(delivered))
        await client.aclose()

    asyncio.run(run())


def test_dns_rebinding_after_registration_fails_the_delivery(rt_url: str) -> None:
    tenant, admin = str(uuid.uuid4()), str(uuid.uuid4())
    answers = {"hooks.example.com": ["93.184.216.34"]}

    async def dns(host: str) -> list[str]:
        return answers.get(host, [])

    async def run() -> None:
        rx = Receiver([])
        client = httpx.AsyncClient(transport=httpx.MockTransport(rx.handler))
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            ep = await register(
                c,
                tenant_id=tenant,
                url="https://hooks.example.com/z",
                event_types=["com.zoiko.id.mandate.revoked.v1"],
                key_id="k",
                created_by=admin,
                resolve=dns,
            )
            await verify(c, ep, client=client, resolve=dns, sign=KEY.sign)
            await route(c, event(tenant))
            answers["hooks.example.com"] = ["169.254.169.254"]  # now points at cloud metadata
            t0 = datetime.now(UTC) + timedelta(seconds=1)
            assert await deliver_due(c, client=client, resolve=dns, sign=KEY.sign, now=t0) == {
                "FAILED": 1
            }
            assert rx.seen == []  # nothing was sent
        await client.aclose()

    asyncio.run(run())
