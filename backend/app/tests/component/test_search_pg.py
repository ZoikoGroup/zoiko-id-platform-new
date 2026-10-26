"""Global search over HTTP (CR-020; ZID-UX-001 §3.1, §25, UX-01).

Search is authorization-aware and tenant-scoped: a type the caller may not read contributes nothing, another
tenant's objects never appear, and short or hostile queries are harmless.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import httpx
from admin_fixture import make_admin
from conftest import CELL, ctx, engine
from fastapi.testclient import TestClient
from sqlalchemy import text
from zid_crypto import KeyRing, LocalSigner
from zid_db import tenant_transaction
from zid_outbox import CloudEvent
from zid_platform.main import create_app
from zid_platform.modules.notification.ports import alert_from_event
from zid_platform.modules.principal.domain.principal import (
    Principal,
    PrincipalStatus,
    PrincipalType,
)
from zid_platform.modules.principal.infrastructure.principal_store import (
    create_principal,
    load_principal,
)
from zid_platform.modules.search.domain.hits import clean, rank, score
from zid_platform.modules.session.domain.session import Session, SessionStatus
from zid_platform.modules.session.infrastructure.session_store import create_session
from zid_platform.modules.session.ports import issue_access_token
from zid_platform.modules.webhook.infrastructure.delivery import register
from zid_platform.settings import Settings

ISS, AUD = "https://id.zoiko.test", "https://api.zoiko.test"
EVENT = "com.zoiko.id.mandate.revoked.v1"


def uid() -> str:
    return str(uuid.uuid4())


async def dns(host: str) -> list[str]:
    return ["93.184.216.34"]


def test_ranking_and_cleaning() -> None:
    assert score("pay", "Payroll Pro") == 1
    assert score("pro", "Payroll Pro") == 2
    assert score("roll", "Payroll Pro") == 3
    assert score("zzz", "Payroll Pro") is None
    rows = [("1", "Billing payroll", ""), ("2", "Payroll", ""), ("3", "Other", "")]
    assert [h.id for h in rank("payroll", rows, type="x", href="/x")] == ["2", "1"]
    assert clean("  a\x00b\n  ") == "ab"
    assert len(clean("x" * 500)) == 80


def test_search_is_authorized_tenant_scoped_and_ranked(rt_url: str, owner_url: str) -> None:
    tenant, other, admin, bob = uid(), uid(), uid(), uid()
    ring = KeyRing(LocalSigner("issuer-1"))
    revoked = CloudEvent(
        id=uid(),
        type="com.zoiko.id.webhook.delivery.exhausted.v1",
        source="urn:zoiko:id:webhook",
        subject="webhook_delivery/d1",
        time=datetime.now(UTC),
        tenantid=tenant,
        cellid="uk-1",
        aggregatetype="webhook_delivery",
        aggregateid="d1",
        aggregateversion=1,
        data={},
        correlationid="c1",
    )

    async def seed() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            for p in (admin, bob):
                await create_principal(
                    c, Principal(p, tenant, PrincipalType.HUMAN, PrincipalStatus.ACTIVE, "uk-1")
                )
            # the admin may read webhooks and alerts only: not applications, connectors or domains
            await make_admin(
                c,
                tenant,
                admin,
                ["webhook.endpoint.read", "notification.alert.read"],
                ["webhook_endpoint:*", "alert:*"],
            )
            await register(
                c,
                tenant_id=tenant,
                url="https://payroll.example.com/zoiko",
                event_types=[EVENT],
                key_id="k1",
                created_by=admin,
                resolve=dns,
            )
            await alert_from_event(c, revoked, now=datetime.now(UTC))
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(other)) as c:
            await register(
                c,
                tenant_id=other,
                url="https://payroll.other-tenant.example.com/hook",
                event_types=[EVENT],
                key_id="k2",
                created_by=uid(),
                resolve=dns,
            )
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

    def find(q: str, headers: dict[str, str]) -> httpx.Response:
        return http.get("/v1/search", params={"q": q}, headers=headers)

    with TestClient(app, base_url=ISS) as http:
        assert http.get("/v1/search", params={"q": "pay"}).status_code == 401  # needs a session

        hits = find("payroll", adm).json()["items"]
        assert [(h["type"], h["label"]) for h in hits] == [
            ("webhook", "https://payroll.example.com/zoiko")
        ]  # not the other tenant's endpoint
        assert hits[0]["group"] == "Webhook endpoints" and hits[0]["href"] == "/admin/developers"

        alerts = find("webhook delivery", adm).json()["items"]
        assert [h["type"] for h in alerts] == ["alert"] and alerts[0]["href"] == "/alerts"

        # a caller with no authority gets an empty, ordinary answer: nothing leaks, nothing errors
        none = find("payroll", nobody)
        assert none.status_code == 200 and none.json()["items"] == []

        # too short: not searched; hostile input is just text
        assert find("p", adm).json()["items"] == []
        r = find("'; DROP TABLE x; --%_", adm)
        assert r.status_code == 200 and r.json()["items"] == []
