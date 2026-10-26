"""Webhook management API over HTTP (CR-018; ZID-EVENTS-001 §14-20; ZID-UX-001 §18).

Full app against PostgreSQL with RLS and real access tokens. Every call goes through the protected-action
PDP with a real authority grant (`make_admin`); a principal without authority is refused. DNS and the
receiver are injected, so no test touches the network.
"""

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
EVENT = "com.zoiko.id.mandate.revoked.v1"
ACTIONS = ["webhook.endpoint.read", "webhook.endpoint.manage"]
RESOURCES = ["webhook_endpoint:*", "webhook_delivery:*"]


def uid() -> str:
    return str(uuid.uuid4())


async def public_dns(host: str) -> list[str]:
    return {"hooks.example.com": ["93.184.216.34"], "rebind.example.com": ["10.0.0.7"]}.get(
        host, []
    )


def echo_receiver(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    return httpx.Response(200, json={"challenge": body.get("challenge")})


def test_webhook_endpoint_lifecycle_over_http(rt_url: str, owner_url: str) -> None:
    tenant, admin, bob = uid(), uid(), uid()
    ring = KeyRing(LocalSigner("issuer-1"))

    async def seed() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            for p in (admin, bob):
                await create_principal(
                    c, Principal(p, tenant, PrincipalType.HUMAN, PrincipalStatus.ACTIVE, "uk-1")
                )
            await make_admin(c, tenant, admin, ACTIONS, RESOURCES)
        async with engine(owner_url) as eng, eng.begin() as c:
            await c.execute(
                text(
                    "INSERT INTO agent.writer_fence (tenant_id, cell_id, epoch) VALUES (:t, :c, 1)"
                ),
                {"t": uuid.UUID(tenant), "c": uuid.UUID(CELL)},
            )

    async def new_session(p: str) -> str:
        sid, t = uid(), datetime.now(UTC)
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            principal, _ = await load_principal(c, p)
            await create_session(
                c,
                Session(
                    sid,
                    tenant,
                    p,
                    t - timedelta(seconds=30),
                    "native:webauthn",
                    2,
                    principal.security_revision,
                    1,
                    SessionStatus.ACTIVE,
                    t + timedelta(hours=8),
                ),
            )
        return sid

    asyncio.run(seed())
    sessions = {p: asyncio.run(new_session(p)) for p in (admin, bob)}
    app = create_app(
        Settings(
            database_url=rt_url,
            token_issuer=ISS,
            token_audience=AUD,
            public_base_url=AUD,
            environment="test",
            cell_uuid=CELL,
        ),
        keyring=ring,
    )
    app.state.webhook_resolver = public_dns
    app.state.webhook_client = httpx.AsyncClient(transport=httpx.MockTransport(echo_receiver))

    def bearer(sub: str) -> dict[str, str]:
        tok = issue_access_token(
            ring,
            issuer=ISS,
            audience=AUD,
            subject=sub,
            tenant_id=tenant,
            client_id="admin-console",
            now=datetime.now(UTC),
            session_id=sessions[sub],
            scope=frozenset(),
        )
        return {"Authorization": f"Bearer {tok}"}

    adm, nobody = bearer(admin), bearer(bob)
    good = {"url": "https://hooks.example.com/zoiko", "event_types": [EVENT]}

    with TestClient(app, base_url=ISS) as http:
        # --- public key set needs no authentication and carries the signer's key id
        keys = http.get("/v1/webhooks/signing-keys").json()["keys"]
        assert len(keys) == 1 and keys[0]["kid"].startswith("webhook-")

        # --- without authority every protected call is refused
        assert http.get("/v1/webhooks/endpoints", headers=nobody).status_code == 403
        assert http.post("/v1/webhooks/endpoints", headers=nobody, json=good).status_code == 403

        # --- catalogue of subscribable events
        types = http.get("/v1/webhooks/event-types", headers=adm).json()["items"]
        assert EVENT in {t["type"] for t in types}

        # --- validation: plain http, unknown event, private address after DNS resolution
        r = http.post(
            "/v1/webhooks/endpoints",
            headers=adm,
            json={**good, "url": "http://hooks.example.com/x"},
        )
        assert r.status_code == 422 and r.json()["error"]["code"] == "WEBHOOK_URL_INVALID"
        r = http.post("/v1/webhooks/endpoints", headers=adm, json={**good, "event_types": ["nope"]})
        assert r.status_code == 422 and r.json()["error"]["code"] == "WEBHOOK_EVENT_NOT_ALLOWED"
        r = http.post(
            "/v1/webhooks/endpoints",
            headers=adm,
            json={**good, "url": "https://rebind.example.com/x"},
        )
        assert r.status_code == 422 and r.json()["error"]["code"] == "WEBHOOK_DESTINATION_BLOCKED"

        # --- register -> PENDING_VERIFICATION; the verification token never appears in a response
        r = http.post("/v1/webhooks/endpoints", headers=adm, json=good)
        assert r.status_code == 201, r.text
        endpoint = r.json()
        eid = endpoint["endpoint_id"]
        assert endpoint["status"] == "PENDING_VERIFICATION"
        assert "verification_token" not in endpoint and endpoint["signing_key_id"] == keys[0]["kid"]

        # --- verify: the signed challenge is echoed -> ACTIVE
        r = http.post(f"/v1/webhooks/endpoints/{eid}:verify", headers=adm)
        assert r.status_code == 200 and r.json()["status"] == "ACTIVE", r.text

        # --- pause / resume / illegal move / retire
        assert (
            http.post(f"/v1/webhooks/endpoints/{eid}:pause", headers=adm).json()["status"]
            == "PAUSED"
        )
        assert http.post(f"/v1/webhooks/endpoints/{eid}:pause", headers=adm).status_code == 409
        assert (
            http.post(f"/v1/webhooks/endpoints/{eid}:resume", headers=adm).json()["status"]
            == "ACTIVE"
        )
        listed = http.get("/v1/webhooks/endpoints", headers=adm).json()["items"]
        assert [e["endpoint_id"] for e in listed] == [eid]
        assert http.get(f"/v1/webhooks/endpoints/{eid}", headers=adm).json()["status"] == "ACTIVE"
        assert http.get(f"/v1/webhooks/endpoints/{uid()}", headers=adm).status_code == 404

        # --- delivery log starts empty; replaying an unknown delivery is a 404
        assert (
            http.get("/v1/webhooks/deliveries", headers=adm, params={"endpoint_id": eid}).json()[
                "items"
            ]
            == []
        )
        assert http.post(f"/v1/webhooks/deliveries/{uid()}:replay", headers=adm).status_code == 404

        assert (
            http.post(f"/v1/webhooks/endpoints/{eid}:retire", headers=adm).json()["status"]
            == "RETIRED"
        )
        assert http.post(f"/v1/webhooks/endpoints/{eid}:resume", headers=adm).status_code == 409
