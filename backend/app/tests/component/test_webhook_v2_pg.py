"""The /v2 webhook facade over HTTP (ZID-INT-002 sections 03 and 13): same rules as /v1, the document's names and RFC 9457 problems."""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta

import httpx
from admin_fixture import make_admin
from conftest import CELL, ctx, engine
from fastapi.testclient import TestClient
from sqlalchemy import text
from zid_crypto import KeyRing, LocalSigner
from zid_db import tenant_transaction
from zid_platform.main import create_app
from zid_platform.modules.principal.domain.principal import (
    Principal,
    PrincipalStatus,
    PrincipalType,
)
from zid_platform.modules.principal.infrastructure.principal_store import (
    create_principal,
    load_principal,
)
from zid_platform.modules.session.domain.session import Session, SessionStatus
from zid_platform.modules.session.infrastructure.session_store import create_session
from zid_platform.modules.session.ports import issue_access_token
from zid_platform.settings import Settings

ISS, AUD = "https://id.zoiko.test", "https://api.zoiko.test"
ACTIONS = ["webhook.endpoint.read", "webhook.endpoint.manage"]
RESOURCES = ["webhook_endpoint:*", "webhook_delivery:*"]


def uid() -> str:
    return str(uuid.uuid4())


async def public_dns(host: str) -> list[str]:
    return {"hooks.example.com": ["93.184.216.34"], "rebind.example.com": ["10.0.0.7"]}.get(
        host, []
    )


def echo_receiver(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"challenge": json.loads(request.content).get("challenge")})


def test_the_v2_webhook_facade_keeps_the_v1_rules_and_the_documents_names(
    rt_url: str, owner_url: str
) -> None:
    tenant, admin = uid(), uid()
    ring = KeyRing(LocalSigner("issuer-wh2"))

    async def seed() -> str:
        sid, now = uid(), datetime.now(UTC)
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            await create_principal(
                c, Principal(admin, tenant, PrincipalType.HUMAN, PrincipalStatus.ACTIVE, "uk-1")
            )
            await make_admin(c, tenant, admin, ACTIONS, RESOURCES)
            principal, _ = await load_principal(c, admin)
            await create_session(
                c,
                Session(
                    sid, tenant, admin, now - timedelta(seconds=30), "native:webauthn", 2,
                    principal.security_revision, 1, SessionStatus.ACTIVE, now + timedelta(hours=8),
                ),
            )  # fmt: skip
        async with engine(owner_url) as eng, eng.begin() as c:
            await c.execute(
                text(
                    "INSERT INTO agent.writer_fence (tenant_id, cell_id, epoch) VALUES (:t, :c, 1)"
                ),
                {"t": uuid.UUID(tenant), "c": uuid.UUID(CELL)},
            )
        return sid

    sid = asyncio.run(seed())
    app = create_app(
        Settings(
            database_url=rt_url, token_issuer=ISS, token_audience=AUD, public_base_url=AUD,
            environment="test", cell_uuid=CELL,
        ),
        keyring=ring,
    )  # fmt: skip
    app.state.webhook_resolver = public_dns
    app.state.webhook_client = httpx.AsyncClient(transport=httpx.MockTransport(echo_receiver))
    tok = issue_access_token(
        ring, issuer=ISS, audience=AUD, subject=admin, tenant_id=tenant, client_id="admin-console",
        now=datetime.now(UTC), session_id=sid, scope=frozenset(),
    )  # fmt: skip
    adm = {"Authorization": f"Bearer {tok}"}
    good = {"url": "https://hooks.example.com/zoiko", "event_types": ["credential.status_changed"]}

    with TestClient(app, base_url=ISS) as http:
        # an Idempotency-Key is required, and a problem is RFC 9457
        no_key = http.post("/v2/webhook-endpoints", headers=adm, json=good)
        assert no_key.status_code == 422 and no_key.headers["content-type"].startswith(
            "application/problem+json"
        )

        # the v1 destination rules still apply: private address after DNS resolution is blocked
        blocked = http.post(
            "/v2/webhook-endpoints", headers={**adm, "Idempotency-Key": "wh-key-0001"},
            json={**good, "url": "https://rebind.example.com/x"},
        )  # fmt: skip
        assert (
            blocked.status_code == 422 and blocked.json()["code"] == "webhook_destination_blocked"
        )

        made = http.post(
            "/v2/webhook-endpoints", headers={**adm, "Idempotency-Key": "wh-key-0002"}, json=good
        )
        assert made.status_code == 201, made.text
        e = made.json()
        assert e["status"] == "PENDING_VERIFICATION" and "verification_token" not in e
        assert e["event_types"] == [
            "credential.status_changed"
        ]  # the document's name, same subscription
        assert e["request_id"].startswith("req_")

        # replay of the same key and body: the same endpoint, not a second one
        again = http.post(
            "/v2/webhook-endpoints", headers={**adm, "Idempotency-Key": "wh-key-0002"}, json=good
        )
        assert again.json()["endpoint_id"] == e["endpoint_id"]
        clash = http.post(
            "/v2/webhook-endpoints", headers={**adm, "Idempotency-Key": "wh-key-0002"},
            json={**good, "max_inflight": 9},
        )  # fmt: skip
        assert clash.status_code == 409 and clash.json()["code"] == "idempotency_conflict"

        listed = http.get("/v2/webhook-endpoints", headers=adm).json()
        assert [x["endpoint_id"] for x in listed["items"]] == [e["endpoint_id"]]
        assert listed["items"][0]["event_types"] == ["credential.status_changed"]

        # an event that does not exist is not found, in either direction
        assert http.get(f"/v2/events/{uid()}", headers=adm).status_code == 404
        assert http.post(f"/v2/events/{uid()}/replay", headers=adm).status_code == 404
        # an event type nobody can subscribe to is refused
        bad = http.post(
            "/v2/webhook-endpoints", headers={**adm, "Idempotency-Key": "wh-key-0003"},
            json={**good, "event_types": ["not.an.event"]},
        )  # fmt: skip
        assert bad.status_code == 422 and bad.json()["code"] == "webhook_event_not_allowed"
