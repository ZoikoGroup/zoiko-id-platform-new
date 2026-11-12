"""HR lifecycle, connector freshness and tenant offboarding (G-W5; OVERLAY-001 §10.1, §22, §24, §31;
OVL-09, OVL-28, OVL-30).

Joiner -> MEMBER membership (no authority); mover -> security revision bumped (sessions and protected
decisions re-read state); leave -> suspended then restored; leaver -> suspended with its sessions
revoked. A connector past its freshness bound drops to DEGRADED. Offboarding (second administrator)
drains every integration and session and closes the tenant, which then no longer authenticates.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from conftest import ctx, engine
from fastapi.testclient import TestClient
from onboarding_fixture import AUD, ISS, approval, onboard, uid
from scim_fixture import KEY
from sqlalchemy import text
from zid_db import tenant_transaction
from zid_platform.main import create_app
from zid_platform.modules.integration.application.service import stale_sweep
from zid_platform.modules.onboarding.ports import offboarding_pass
from zid_platform.modules.principal.domain.principal import (
    Principal,
    PrincipalStatus,
    PrincipalType,
)
from zid_platform.modules.principal.infrastructure.principal_store import (
    create_principal,
    load_principal,
)
from zid_platform.modules.tenant.application.routing import forget
from zid_platform.settings import Settings


def test_hr_lifecycle_freshness_and_offboarding(rt_url: str, owner_url: str) -> None:
    o = onboard(rt_url)
    app = create_app(
        Settings(
            database_url=rt_url,
            token_issuer=ISS,
            token_audience=AUD,
            public_base_url=AUD,
            environment="test",
            cell_id="uk-1",
        ),
        keyring=o.ring,
    )
    hris_client, svc = f"hris-{uid()[:8]}", uid()

    async def service_principal() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(o.tenant)) as c:
            await create_principal(
                c,
                Principal(svc, o.tenant, PrincipalType.APPLICATION, PrincipalStatus.ACTIVE, "uk-1"),
            )

    asyncio.run(service_principal())

    async def principal(p: str) -> Any:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(o.tenant)) as c:
            return (await load_principal(c, p))[0]

    with TestClient(app) as api:
        r = api.post(
            "/provisioning/sources",
            json={"jwks": {"keys": [KEY]}, "display_name": "Workday"},
            headers=o.bearer(o.owner),
        )
        assert r.status_code == 201, r.text
        hris_client = r.json()["client_id"]  # the platform created the connector's client
        source_ref = f"provisioning_source:{r.json()['source_id']}"
        r = api.post(
            "/v1/integration/connectors",
            json={
                "connector_type": "HRIS",
                "display_name": "Workday",
                "source_ref": source_ref,
                "capabilities": ["HR_LIFECYCLE"],
                "freshness_minutes": 1,
            },
            headers=o.bearer(o.owner),
        )
        assert r.status_code == 201, r.text
        cid = r.json()["connector_id"]
        hris = o.bearer(svc, scope="scim:provision", client=hris_client)
        r = api.post(
            "/scim/v2/Users",
            json={
                "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
                "userName": "jd",
                "externalId": "w1",
            },
            headers=hris,
        )
        assert r.status_code == 201, r.text
        user = r.json()["id"]
        person = None

        def event(name: str) -> dict[str, Any]:
            r = api.post(
                "/provisioning/lifecycle-events",
                json={"user_id": user, "event": name},
                headers=hris,
            )
            assert r.status_code == 200, r.text
            return dict(r.json())

        out = event("JOINER")
        person = out["principal_id"]
        assert out["membership"] == "CREATED"
        assert event("JOINER")["membership"] == "EXISTING"
        members = api.get("/v1/tenant/members", headers=o.bearer(o.owner)).json()["items"]
        joined = next(m for m in members if m["principal_id"] == person)
        assert (joined["role"], joined["origin"]) == ("MEMBER", "PROVISIONING")  # never authority

        before = asyncio.run(principal(person)).security_revision
        assert event("MOVER")["security_revision"] > before  # OVL-28
        event("LEAVE_START")
        assert asyncio.run(principal(person)).status is PrincipalStatus.SUSPENDED
        event("LEAVE_END")
        assert asyncio.run(principal(person)).status is PrincipalStatus.ACTIVE
        assert "grants_revoked" in event("LEAVER")
        assert asyncio.run(principal(person)).status is PrincipalStatus.SUSPENDED  # OVL-09

        r = api.post(
            "/provisioning/lifecycle-events",
            json={"user_id": str(uuid.uuid4()), "event": "MOVER"},
            headers=hris,
        )
        assert r.status_code == 404  # only resources this source provisioned

    async def sweep() -> None:
        async with engine(owner_url) as eng, eng.begin() as c:
            stale = await stale_sweep(c, datetime.now(UTC) + timedelta(minutes=5))
            assert cid in stale

    asyncio.run(sweep())
    with TestClient(app) as api:
        c = api.get(f"/v1/integration/connectors/{cid}", headers=o.bearer(o.owner)).json()
        assert c["health"] == "DEGRADED"

        # --- offboarding: governed request, then the drain closes the tenant -----------------------
        rid = approval(
            api,
            o,
            owner_url,
            action="tenant.lifecycle.manage",
            resource=f"tenant:{o.tenant}",
            change="OFFBOARD",
        )
        r = api.post(
            "/v1/tenant:offboard", json={"approval_request_id": rid}, headers=o.bearer(o.owner)
        )
        assert r.status_code == 200 and r.json()["status"] == "OFFBOARDING", r.text

    async def others_quarantined(c: Any) -> int:
        return int(
            (
                await c.execute(
                    text(
                        "SELECT count(*) FROM platform.integration_connector "
                        "WHERE health = 'QUARANTINED' AND tenant_id <> :t"
                    ),
                    {"t": uuid.UUID(o.tenant)},
                )
            ).scalar_one()
        )

    async def drain() -> None:
        async with engine(owner_url) as eng:
            async with eng.begin() as c:
                before = await others_quarantined(c)
                done = await offboarding_pass(c, datetime.now(UTC))
                # the worker bypasses row-level security: closing one tenant must leave every other tenant's connectors alone
                assert await others_quarantined(c) == before
                mine = next(d for d in done if d["tenant_id"] == o.tenant)
                assert mine["status"] == "CLOSED" and mine["connectors_quarantined"] == 1
                assert int(mine["sessions_revoked"]) >= 2  # type: ignore[call-overload]
            async with eng.begin() as c:
                row = (
                    await c.execute(
                        text(
                            "SELECT (SELECT status FROM platform.tenant WHERE tenant_id = :t) AS tenant_status, "
                            "(SELECT status FROM platform.provisioning_source WHERE tenant_id = :t) AS source_status"
                        ),
                        {"t": uuid.UUID(o.tenant)},
                    )
                ).one()
                assert (row.tenant_status, row.source_status) == ("CLOSED", "SUSPENDED")

    asyncio.run(drain())
    forget(o.tenant)
    with TestClient(app) as api:
        r = api.get("/v1/tenant", headers=o.bearer(o.owner))
        # the session was revoked by the drain (401); a fresh login would meet TENANT_CLOSED (403)
        assert r.status_code in (401, 403)
