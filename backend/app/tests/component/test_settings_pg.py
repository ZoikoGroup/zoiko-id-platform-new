"""Tenant settings over HTTP (CR-022; ZID-UX-001 §19, §20, UX-06).

Versioned, validated settings with visible conflicts, and the effects that make them real: session limits
shorten sessions on every request, muted alert kinds stop non-mandatory alerts (never mandatory ones),
branding appears on the workspace sign-in lookup, and the cryptography view shows no key material.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from conftest import CELL, ctx, engine
from fastapi.testclient import TestClient
from onboarding_fixture import AUD, ISS, onboard
from sqlalchemy import text
from zid_db import tenant_transaction
from zid_outbox import CloudEvent
from zid_platform.main import create_app
from zid_platform.modules.configuration.ports import muted_alert_kinds, session_limits
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
from zid_platform.modules.tenant.infrastructure.tenant_store import insert_workspace
from zid_platform.settings import Settings


def uid() -> str:
    return str(uuid.uuid4())


def event(tenant: str, etype: str, ref: str) -> CloudEvent:
    return CloudEvent(
        id=uid(),
        type=etype,
        source="urn:zoiko:id:test",
        subject=ref,
        time=datetime.now(UTC),
        tenantid=tenant,
        cellid="uk-1",
        aggregatetype=ref.split("/")[0],
        aggregateid=ref.split("/")[-1],
        aggregateversion=1,
        data={},
        correlationid="c1",
    )


def test_settings_versioning_validation_and_effects(rt_url: str, owner_url: str) -> None:
    o = onboard(
        rt_url,
        extra_actions=["configuration.read", "configuration.manage"],
        extra_resources=["settings:*"],
    )
    tenant, admin, sleepy, nobody = o.tenant, o.owner, o.second, uid()
    slug = f"acme-{uid()[:8]}"

    async def seed() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            await create_principal(
                c, Principal(nobody, tenant, PrincipalType.HUMAN, PrincipalStatus.ACTIVE, "uk-1")
            )
            await insert_workspace(
                c, workspace=slug, tenant_id=tenant, client_id="console-x", name="Acme"
            )
        async with engine(owner_url) as eng, eng.begin() as c:
            await c.execute(
                text(
                    "INSERT INTO authn.client_directory (client_id, tenant_id) VALUES ('console-x', :t) ON CONFLICT DO NOTHING"
                ),
                {"t": uuid.UUID(tenant)},
            )
            await c.execute(
                text(
                    "INSERT INTO agent.writer_fence (tenant_id, cell_id, epoch) VALUES (:t, :c, 1) ON CONFLICT DO NOTHING"
                ),
                {"t": uuid.UUID(tenant), "c": uuid.UUID(CELL)},
            )

    async def stale_session(auth_age: timedelta, idle_age: timedelta) -> str:
        sid, now = uid(), datetime.now(UTC)
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            principal, _ = await load_principal(c, sleepy)
            await create_session(
                c,
                Session(
                    sid,
                    tenant,
                    sleepy,
                    now - auth_age,
                    "native:webauthn",
                    2,
                    principal.security_revision,
                    1,
                    SessionStatus.ACTIVE,
                    now + timedelta(hours=6),
                    now - idle_age,
                ),
            )
        return sid

    asyncio.run(seed())
    app = create_app(
        Settings(
            database_url=rt_url,
            token_issuer=ISS,
            token_audience=AUD,
            public_base_url=AUD,
            environment="test",
            cell_id="uk-1",
            cell_uuid=CELL,
        ),
        keyring=o.ring,
    )
    adm = o.bearer(admin)

    def token_for(session_id: str) -> dict[str, str]:
        tok = issue_access_token(
            o.ring,
            issuer=ISS,
            audience=AUD,
            subject=sleepy,
            tenant_id=tenant,
            client_id="console",
            now=datetime.now(UTC),
            session_id=session_id,
            scope=frozenset(),
        )
        return {"Authorization": f"Bearer {tok}"}

    def put(http: TestClient, area: str, rev: int, value: dict) -> object:  # type: ignore[type-arg]
        return http.put(
            f"/v1/settings/{area}", headers=adm, json={"expected_revision": rev, "value": value}
        )

    with TestClient(app, base_url=ISS) as http:
        first = http.get("/v1/settings", headers=adm).json()["areas"]
        assert {a: first[a]["revision"] for a in first} == {
            "AUTHENTICATION": 0,
            "NOTIFICATIONS": 0,
            "BRANDING": 0,
        }
        assert http.get("/v1/settings", headers=o.bearer(nobody)).status_code == 403  # no authority
        assert (
            http.put(
                "/v1/settings/BRANDING",
                headers=o.bearer(nobody),
                json={"expected_revision": 0, "value": {}},
            ).status_code
            == 403
        )

        # --- BRANDING: anti-spoofing, closed vocabularies, https only
        bad_name = put(http, "BRANDING", 0, {"display_name": "Zoiko Support Team"})
        assert (
            bad_name.status_code == 422 and bad_name.json()["error"]["code"] == "BRANDING_SPOOFING"
        )  # type: ignore[attr-defined]
        assert put(http, "BRANDING", 0, {"accent": "hotpink"}).status_code == 422  # type: ignore[attr-defined]
        assert put(http, "BRANDING", 0, {"support_url": "http://help.acme.test"}).status_code == 422  # type: ignore[attr-defined]
        assert put(http, "BRANDING", 0, {"surprise": True}).status_code == 422  # type: ignore[attr-defined]
        ok = put(
            http,
            "BRANDING",
            0,
            {
                "display_name": "Acme Global",
                "accent": "teal",
                "support_url": "https://help.acme.test",
            },
        )
        assert ok.status_code == 200 and ok.json()["revision"] == 1  # type: ignore[attr-defined]

        # --- UX-06: a stale edit is a visible conflict, never a silent overwrite
        stale = put(http, "BRANDING", 0, {"display_name": "Someone else's edit"})
        assert (
            stale.status_code == 409 and stale.json()["error"]["code"] == "CONFIGURATION_CONFLICT"
        )  # type: ignore[attr-defined]
        assert "revision 1" in stale.json()["error"]["message"]  # type: ignore[attr-defined]
        again = put(http, "BRANDING", 1, {"display_name": "Acme Global", "accent": "violet"})
        assert again.json()["revision"] == 2  # type: ignore[attr-defined]
        hist = http.get("/v1/settings/BRANDING/history", headers=adm).json()["items"]
        assert [h["revision"] for h in hist] == [2, 1] and hist[1]["value"]["accent"] == "teal"

        # --- the sign-in lookup shows the tenant's chosen presentation, and only that
        look = http.get(f"/v1/workspaces/{slug}").json()
        assert look["display_name"] == "Acme Global" and look["branding"]["accent"] == "violet"
        assert set(look) == {"workspace", "display_name", "console_client_id", "branding"}

        # --- the fuller theme (colour, welcome text) is public to the sign-in page by the workspace's client, and only that
        themed = put(
            http,
            "BRANDING",
            2,
            {
                "display_name": "Acme Global",
                "accent_hex": "#1a73e8",
                "welcome_text": "Welcome back",
            },
        )
        assert themed.status_code == 200, themed.text  # type: ignore[attr-defined]
        brand = http.get(
            "/v1/workspace-brand", params={"client_id": look["console_client_id"]}
        ).json()
        assert brand == {
            "accent": "blue",
            "accent_hex": "#1a73e8",
            "welcome_text": "Welcome back",
            "logo": "",
            "support_url": "",
        }
        nobody = http.get("/v1/workspace-brand", params={"client_id": "no-such-client"}).json()
        assert nobody["accent_hex"] == "" and nobody["welcome_text"] == ""
        assert put(http, "BRANDING", 3, {"accent_hex": "#ffff00"}).status_code == 422  # type: ignore[attr-defined]

        # --- NOTIFICATIONS: mandatory notices can never be muted
        locked = put(http, "NOTIFICATIONS", 0, {"muted_kinds": ["authority_root.revoked"]})
        assert (
            locked.status_code == 422 and locked.json()["error"]["code"] == "NOTIFICATION_MANDATORY"
        )  # type: ignore[attr-defined]
        assert put(http, "NOTIFICATIONS", 0, {"muted_kinds": ["not.a.kind"]}).status_code == 422  # type: ignore[attr-defined]
        assert (
            put(http, "NOTIFICATIONS", 0, {"muted_kinds": ["mandate.revoked"]}).status_code == 200
        )  # type: ignore[attr-defined]

        # --- AUTHENTICATION: shorten only, and consistent
        assert put(http, "AUTHENTICATION", 0, {"session_absolute_hours": 13}).status_code == 422  # type: ignore[attr-defined]
        assert put(http, "AUTHENTICATION", 0, {"session_idle_minutes": 60}).status_code == 422  # type: ignore[attr-defined]
        assert (
            put(
                http, "AUTHENTICATION", 0, {"session_absolute_hours": 1, "session_idle_minutes": 90}
            ).status_code
            == 422
        )  # type: ignore[attr-defined]

        # sessions older than the tenant's limits are fine before the limit exists
        old = asyncio.run(stale_session(timedelta(hours=3), timedelta(minutes=20)))
        assert http.get("/v1/settings", headers=token_for(old)).status_code == 200
        limits = put(
            http, "AUTHENTICATION", 0, {"session_absolute_hours": 2, "session_idle_minutes": 15}
        )
        assert limits.status_code == 200  # type: ignore[attr-defined]

        # --- enforced on every request: that same session is now over the tenant's lifetime / idle limit
        over = http.get("/v1/settings", headers=token_for(old))
        assert over.status_code == 401, over.text  # type: ignore[attr-defined]
        idle_only = asyncio.run(stale_session(timedelta(minutes=30), timedelta(minutes=20)))
        assert http.get("/v1/settings", headers=token_for(idle_only)).status_code == 401
        fresh = asyncio.run(stale_session(timedelta(minutes=30), timedelta(minutes=2)))
        assert http.get("/v1/settings", headers=token_for(fresh)).status_code == 200

        # --- the effects other modules read through ports
        async def effects() -> None:
            async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
                assert await session_limits(c) == (timedelta(hours=2), timedelta(minutes=15))
                muted = await muted_alert_kinds(c)
                assert muted == frozenset({"mandate.revoked"})
                now = datetime.now(UTC)
                assert (
                    await alert_from_event(
                        c,
                        event(tenant, "com.zoiko.id.mandate.revoked.v1", "mandate/m1"),
                        now=now,
                        muted=muted,
                    )
                    is None
                )  # muted, non-mandatory
                assert (
                    await alert_from_event(
                        c,
                        event(
                            tenant, "com.zoiko.id.authority_root.revoked.v1", "authority_root/r1"
                        ),
                        now=now,
                        muted=muted,
                    )
                    is not None
                )  # mandatory: never muted

        asyncio.run(effects())

        # --- cryptography overview: who holds the keys and which algorithms, never key material
        crypto = http.get("/v1/settings/cryptography", headers=adm)
        assert crypto.status_code == 200
        body = crypto.json()
        assert (
            body["key_mode"] == "PLATFORM_MANAGED"
            and body["customer_managed_keys"]["byok"]["available"] is False
        )
        assert body["purposes"] and all(p["algorithms"] for p in body["purposes"])
        assert "BEGIN" not in crypto.text and "PRIVATE" not in crypto.text.upper()
