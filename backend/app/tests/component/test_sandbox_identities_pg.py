"""Developer sandbox test identities on a real database: created, listed, retired, expired, and never able to sign in."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from conftest import ctx, engine
from fastapi.testclient import TestClient
from onboarding_fixture import AUD, ISS, onboard
from sqlalchemy import text
from zid_db import tenant_transaction
from zid_platform.main import create_app
from zid_platform.modules.integration.application.sandbox import expire_due
from zid_platform.settings import Settings


def test_test_identities_are_created_listed_retired_and_expire(rt_url: str, owner_url: str) -> None:
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
    h = o.bearer(o.owner)
    with TestClient(app) as api:
        r = api.post(
            "/v1/integration/sandbox-identities",
            json={"prefix": "payroll", "count": 3, "days": 7},
            headers=h,
        )
        assert r.status_code == 201, r.text
        made = r.json()["items"]
        assert [m["label"] for m in made] == ["payroll-01", "payroll-02", "payroll-03"]
        again = api.post(
            "/v1/integration/sandbox-identities", json={"prefix": "payroll", "count": 1}, headers=h
        ).json()["items"]
        assert again[0]["label"] == "payroll-04"  # labels are never reused

        listed = api.get("/v1/integration/sandbox-identities", headers=h).json()
        assert len(listed["items"]) == 4 and {i["status"] for i in listed["items"]} == {"LIVE"}

        bad = api.post("/v1/integration/sandbox-identities", json={"count": 21}, headers=h)
        assert bad.status_code == 422
        over = api.post("/v1/integration/sandbox-identities", json={"count": 20}, headers=h)
        assert over.status_code == 201
        over = api.post(
            "/v1/integration/sandbox-identities", json={"prefix": "more", "count": 20}, headers=h
        )
        assert over.status_code == 201
        full = api.post(
            "/v1/integration/sandbox-identities", json={"prefix": "extra", "count": 10}, headers=h
        )
        assert full.status_code == 409 and full.json()["error"]["code"] == "SANDBOX_LIMIT"

        first = made[0]["principal_id"]
        assert (
            api.post(f"/v1/integration/sandbox-identities/{first}:retire", headers=h).status_code
            == 200
        )
        assert (
            api.post(f"/v1/integration/sandbox-identities/{first}:retire", headers=h).status_code
            == 404
        )

    async def expire() -> tuple[int, str, int]:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(o.tenant)) as c:
            await c.execute(
                text(
                    "UPDATE platform.sandbox_identity SET expires_at = now() - interval '1 hour' WHERE label = 'payroll-02'"
                )
            )
            n = await expire_due(c, datetime.now(UTC) + timedelta(seconds=1))
            st = (
                await c.execute(
                    text(
                        "SELECT status::text FROM core.principal p JOIN platform.sandbox_identity s USING (principal_id) WHERE s.label = 'payroll-02'"
                    )
                )
            ).scalar_one()
            passkeys = (
                await c.execute(
                    text(
                        "SELECT count(*) FROM platform.sandbox_identity s JOIN core.principal p USING (principal_id) WHERE p.status = 'ACTIVE'"
                    )
                )
            ).scalar_one()
            return n, st, passkeys

    n, status, live = asyncio.run(expire())
    assert n == 1 and status == "DISABLED"
    assert live == 4 + 20 + 20 - 2  # the retired one and the expired one are no longer active
