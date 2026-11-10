"""Offboarding one company must touch only that company, even when the worker that runs it bypasses row-level security.

The offboarding drain is run by the worker role, which sweeps every tenant and so BYPASSES row-level security. A drain that listed "every connector" or
"every live session" and relied on row-level security to narrow it would, under that role, quarantine every company's connectors, retire every company's
applications and sign every company out. These tests run the drain on a connection that bypasses row-level security, exactly as the worker does, with
two companies, and prove the other company is left alone. (`owner_url` is a superuser connection: it bypasses row-level security like the worker role.)"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any

from conftest import engine
from fastapi.testclient import TestClient
from onboarding_fixture import AUD, ISS, Onboarded, approval, onboard
from scim_fixture import KEY
from sqlalchemy import text
from zid_platform.main import create_app
from zid_platform.modules.onboarding.ports import offboarding_pass
from zid_platform.settings import Settings


def _app(rt_url: str, o: Onboarded) -> Any:
    return create_app(
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


def _give_a_connector_and_an_application(
    api: TestClient, o: Onboarded, name: str
) -> tuple[str, str]:
    h = o.bearer(o.owner)
    source = api.post(
        "/provisioning/sources", json={"jwks": {"keys": [KEY]}, "display_name": name}, headers=h
    )
    assert source.status_code == 201, source.text
    connector = api.post(
        "/v1/integration/connectors",
        json={
            "connector_type": "HRIS",
            "display_name": name,
            "source_ref": f"provisioning_source:{source.json()['source_id']}",
            "capabilities": ["HR_LIFECYCLE"],
            "freshness_minutes": 60,
        },
        headers=h,
    )
    assert connector.status_code == 201, connector.text
    application = api.post(
        "/v1/integration/applications",
        json={"name": f"{name} app", "environment": "PROD", "protocols": ["OIDC", "AUTHZEN"]},
        headers=h,
    )
    assert application.status_code == 201, application.text
    return connector.json()["connector_id"], application.json()["application_id"]


def _connector_health(api: TestClient, o: Onboarded, cid: str) -> str:
    return str(
        api.get(f"/v1/integration/connectors/{cid}", headers=o.bearer(o.owner)).json()["health"]
    )


def _application_status(api: TestClient, o: Onboarded, aid: str) -> str:
    items = api.get("/v1/integration/applications", headers=o.bearer(o.owner)).json()["items"]
    return str(next(a for a in items if a["application_id"] == aid)["status"])


async def _session_statuses(owner_url: str, tenant: str) -> list[str]:
    async with engine(owner_url) as eng, eng.begin() as c:
        rows = (
            await c.execute(
                text("SELECT status FROM authn.session WHERE tenant_id = :t ORDER BY status"),
                {"t": uuid.UUID(tenant)},
            )
        ).all()
    return [r.status for r in rows]


def test_offboarding_one_company_leaves_every_other_company_untouched(
    rt_url: str, owner_url: str
) -> None:
    leaving, staying = onboard(rt_url), onboard(rt_url)
    # each test company signs its tokens with its own key, so each gets its own app instance; both use the same database
    with TestClient(_app(rt_url, leaving)) as api, TestClient(_app(rt_url, staying)) as api_staying:
        l_connector, l_application = _give_a_connector_and_an_application(api, leaving, "Leaving")
        s_connector, s_application = _give_a_connector_and_an_application(
            api_staying, staying, "Staying"
        )
        stay_sessions_before = asyncio.run(_session_statuses(owner_url, staying.tenant))
        assert stay_sessions_before and set(stay_sessions_before) == {"ACTIVE"}

        rid = approval(
            api,
            leaving,
            owner_url,
            action="tenant.lifecycle.manage",
            resource=f"tenant:{leaving.tenant}",
            change="OFFBOARD",
        )
        started = api.post(
            "/v1/tenant:offboard",
            json={"approval_request_id": rid},
            headers=leaving.bearer(leaving.owner),
        )
        assert started.status_code == 200 and started.json()["status"] == "OFFBOARDING", (
            started.text
        )

        async def drain() -> list[dict[str, object]]:
            async with (
                engine(owner_url) as eng,
                eng.begin() as c,
            ):  # bypasses row-level security, like the worker role
                return await offboarding_pass(c, datetime.now(UTC))

        done = asyncio.run(drain())
        mine = next(d for d in done if d["tenant_id"] == leaving.tenant)
        # exactly what the leaving company had, not what the whole database holds
        assert mine["status"] == "CLOSED"
        assert (mine["connectors_quarantined"], mine["applications_retired"]) == (1, 1), mine
        assert mine["sessions_revoked"] == len(leaving.sessions), mine
        assert [d["tenant_id"] for d in done] == [
            leaving.tenant
        ]  # and the other company was never drained

        # the company that stayed: connector, application and every session exactly as they were
        assert _connector_health(api_staying, staying, s_connector) != "QUARANTINED"
        assert _application_status(api_staying, staying, s_application) != "RETIRED"
        assert asyncio.run(_session_statuses(owner_url, staying.tenant)) == stay_sessions_before

        # and the one that left was drained
        async def left() -> tuple[list[str], list[str]]:
            async with engine(owner_url) as eng, eng.begin() as c:
                health = (
                    await c.execute(
                        text(
                            "SELECT health FROM platform.integration_connector WHERE connector_id = :i"
                        ),
                        {"i": uuid.UUID(l_connector)},
                    )
                ).scalar_one()
                app_status = (
                    await c.execute(
                        text(
                            "SELECT status FROM platform.integration_application WHERE application_id = :i"
                        ),
                        {"i": uuid.UUID(l_application)},
                    )
                ).scalar_one()
            return [health], [app_status]

        health, status = asyncio.run(left())
        assert health == ["QUARANTINED"] and status == ["RETIRED"]
        assert set(asyncio.run(_session_statuses(owner_url, leaving.tenant))) == {"REVOKED"}
