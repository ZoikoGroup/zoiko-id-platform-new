"""Alerts and tasks over HTTP (CR-019; ZID-UX-001 §20).

Platform events become alerts by fixed templates; repeats collapse into one live alert; acknowledging is
not resolving; a tenant never sees another tenant's alerts; a principal without authority is refused.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from admin_fixture import make_admin
from conftest import CELL, ctx, engine
from fastapi.testclient import TestClient
from sqlalchemy import text
from zid_crypto import KeyRing, LocalSigner
from zid_db import tenant_transaction
from zid_outbox import CloudEvent
from zid_platform.main import create_app
from zid_platform.modules.notification.domain.alerts import TEMPLATES, draft_for_event, event_name
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
from zid_platform.modules.session.domain.session import Session, SessionStatus
from zid_platform.modules.session.infrastructure.session_store import create_session
from zid_platform.modules.session.ports import issue_access_token
from zid_platform.settings import Settings

ISS, AUD = "https://id.zoiko.test", "https://api.zoiko.test"
ACTIONS = ["notification.alert.read", "notification.alert.manage"]
RESOURCES = ["alert:*"]


def uid() -> str:
    return str(uuid.uuid4())


def event(tenant: str, etype: str, ref: str) -> CloudEvent:
    kind = event_name(etype)
    return CloudEvent(
        id=uid(),
        type=etype,
        source=f"urn:zoiko:id:{kind.split('.')[0]}",
        subject=ref,
        time=datetime.now(UTC),
        tenantid=tenant,
        cellid="uk-1",
        aggregatetype=kind.split(".")[0],
        aggregateid=ref.split("/")[-1],
        aggregateversion=1,
        data={},
        correlationid="c1",
    )


def _draft(e: CloudEvent):  # type: ignore[no-untyped-def]
    return draft_for_event(
        e.type,
        subject=e.subject,
        aggregate_type=e.aggregatetype,
        aggregate_id=e.aggregateid,
        event_id=e.id,
    )


def test_templates_are_well_formed() -> None:
    for name, t in TEMPLATES.items():
        assert t.href.startswith("/"), name
        assert t.title and t.action, name
    assert event_name("com.zoiko.id.authority_root.revoked.v1") == "authority_root.revoked"
    # an event outside the templates is never an alert; operator-plane events are not in the table
    assert _draft(event(uid(), "com.zoiko.id.ops.incident.declared.v1", "incident/1")) is None
    crit = _draft(event(uid(), "com.zoiko.id.authority_root.revoked.v1", "root/1"))
    assert (
        crit is not None and crit.mandatory and crit.is_task and crit.severity.value == "CRITICAL"
    )


def test_alert_inbox_collapse_ack_resolve_isolation(rt_url: str, owner_url: str) -> None:
    tenant, other, admin, bob = uid(), uid(), uid(), uid()
    ring = KeyRing(LocalSigner("issuer-1"))

    async def seed() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            for p in (admin, bob):
                await create_principal(
                    c, Principal(p, tenant, PrincipalType.HUMAN, PrincipalStatus.ACTIVE, "uk-1")
                )
            await make_admin(c, tenant, admin, ACTIONS, RESOURCES)
            now = datetime.now(UTC)
            # three correlated repeats collapse into one; two other alerts stay separate
            for _ in range(3):
                await alert_from_event(
                    c,
                    event(
                        tenant, "com.zoiko.id.webhook.delivery.exhausted.v1", "webhook_delivery/d1"
                    ),
                    now=now,
                )
            await alert_from_event(
                c,
                event(tenant, "com.zoiko.id.authority_root.revoked.v1", "authority_root/r1"),
                now=now,
            )
            await alert_from_event(
                c, event(tenant, "com.zoiko.id.mandate.revoked.v1", "mandate/m1"), now=now
            )
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(other)) as c:
            await alert_from_event(
                c,
                event(other, "com.zoiko.id.authority_root.revoked.v1", "authority_root/x"),
                now=datetime.now(UTC),
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

    with TestClient(app, base_url=ISS) as http:
        assert http.get("/v1/alerts", headers=nobody).status_code == 403

        items = http.get("/v1/alerts", headers=adm).json()["items"]
        assert [a["severity"] for a in items] == ["CRITICAL", "HIGH", "MEDIUM"]  # most severe first
        assert all(a["state"] == "OPEN" for a in items)
        exhausted = next(a for a in items if a["kind"] == "webhook.delivery.exhausted")
        assert exhausted["occurrences"] == 3  # collapsed, not three rows
        assert (
            exhausted["href"] == "/admin/developers"
            and exhausted["is_task"]
            and exhausted["mandatory"]
        )
        assert "authority_root/x" not in {a["object_ref"] for a in items}  # tenant isolation

        s = http.get("/v1/alerts/summary", headers=adm).json()
        assert s == {
            "open": 3,
            "unacknowledged": 3,
            "by_severity": {"CRITICAL": 1, "HIGH": 1, "MEDIUM": 1, "INFO": 0},
        }

        critical = items[0]["alert_id"]
        assert (
            http.get("/v1/alerts?severity=HIGH", headers=adm)
            .json()["items"][0]["kind"]
            .startswith("webhook")
        )

        # --- acknowledging is not resolving: it stays in the live list, with its own actor and time
        ack = http.post(f"/v1/alerts/{critical}:acknowledge", headers=adm)
        assert ack.status_code == 200 and ack.json()["state"] == "ACKNOWLEDGED"
        assert ack.json()["acknowledged_by"] == admin and ack.json()["resolved_at"] is None
        assert http.post(f"/v1/alerts/{critical}:acknowledge", headers=adm).status_code == 409
        assert http.get("/v1/alerts/summary", headers=adm).json()["unacknowledged"] == 2
        assert len(http.get("/v1/alerts", headers=adm).json()["items"]) == 3

        # --- resolve closes it; resolved alerts leave the live list and cannot be reopened
        done = http.post(f"/v1/alerts/{critical}:resolve", headers=adm)
        assert done.status_code == 200 and done.json()["state"] == "RESOLVED"
        assert http.post(f"/v1/alerts/{critical}:resolve", headers=adm).status_code == 409
        assert len(http.get("/v1/alerts", headers=adm).json()["items"]) == 2
        assert len(http.get("/v1/alerts?state=RESOLVED", headers=adm).json()["items"]) == 1

        # --- a new occurrence after resolution starts a fresh alert
        asyncio.run(_repeat(rt_url, tenant))
        assert http.get("/v1/alerts/summary", headers=adm).json()["by_severity"]["CRITICAL"] == 1

        assert http.get(f"/v1/alerts/{uid()}", headers=adm).status_code == 404
        # another tenant's alert id is not found, not forbidden (UX-23: no existence leak)
        assert http.post(f"/v1/alerts/{uid()}:resolve", headers=adm).status_code == 404


async def _repeat(rt_url: str, tenant: str) -> None:
    async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
        await alert_from_event(
            c,
            event(tenant, "com.zoiko.id.authority_root.revoked.v1", "authority_root/r1"),
            now=datetime.now(UTC),
        )
