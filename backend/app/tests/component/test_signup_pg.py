"""Public company sign-up end to end (G-W0; OVERLAY-001 §4; TENANCY-001 §14).

Sign-up is off by default. When on, a person enters a company name and workspace name and gets a
new tenant with its own console client, a workspace entry other people can look up to sign in, and
a single-use passkey enrollment ticket for its first principal (pending OWNER). In local/test with
development verification, the OWNER is active at once and can administer (invite) the team; the
synthetic root says it is unverified. Taken workspace names are refused.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from conftest import ctx, engine
from fastapi.testclient import TestClient
from onboarding_fixture import AUD, ISS, _session, uid
from sqlalchemy import text
from zid_crypto import KeyRing, LocalSigner
from zid_db import tenant_transaction
from zid_platform.main import create_app
from zid_platform.modules.onboarding.api import signup as signup_api
from zid_platform.modules.session.ports import issue_access_token
from zid_platform.settings import Settings

CB = "http://127.0.0.1:3000/auth/callback"


def settings(rt_url: str, **kw: object) -> Settings:
    return Settings(
        database_url=rt_url,
        token_issuer=ISS,
        token_audience=AUD,
        public_base_url=AUD,
        environment="test",
        cell_id="uk-1",
        tenant_console_redirect_uris=(CB,),
        **kw,  # type: ignore[arg-type]
    )


def test_signup_disabled_by_default(rt_url: str) -> None:
    with TestClient(create_app(settings(rt_url), keyring=KeyRing(LocalSigner("s0")))) as api:
        r = api.post("/v1/signup", json={"company_name": "Acme", "workspace": "acme"})
        assert r.status_code == 403 and r.json()["error"]["code"] == "SIGNUP_DISABLED"


def test_dev_verify_is_refused_outside_local(rt_url: str) -> None:
    with pytest.raises(RuntimeError):
        create_app(
            settings(rt_url, tenant_signup_enabled=True, tenant_signup_dev_verify=True).model_copy(
                update={"environment": "production", "token_issuer": None}
            )
        )


def test_signup_workspace_lookup_and_admin_ready(rt_url: str) -> None:
    signup_api._recent.clear()
    ring = KeyRing(LocalSigner("s1"))
    app = create_app(
        settings(rt_url, tenant_signup_enabled=True, tenant_signup_dev_verify=True), keyring=ring
    )
    ws = f"acme-{uid()[:6]}"
    with TestClient(app) as api:
        r = api.post("/v1/signup", json={"company_name": "Acme Widgets", "workspace": ws, "x": 1})
        assert r.status_code == 422  # no extra fields (never a tenant id)
        r = api.post("/v1/signup", json={"company_name": "Acme Widgets", "workspace": ws})
        assert r.status_code == 201, r.text
        out = r.json()
        assert out["tenant_status"] == "CONFIGURING"
        assert out["organization"] == "DEVELOPMENT_UNVERIFIED"
        assert out["enrollment_ticket"] and out["console_client_id"].startswith("zid-console-")
        r = api.post("/v1/signup", json={"company_name": "Other", "workspace": ws})
        assert r.status_code == 409 and r.json()["error"]["code"] == "TENANT_SLUG_TAKEN"

        r = api.get(f"/v1/workspaces/{ws.upper()}")
        assert r.status_code == 200
        assert r.json() == {
            "workspace": ws,
            "display_name": "Acme Widgets",
            "console_client_id": out["console_client_id"],
        }
        assert api.get("/v1/workspaces/nope-nope").status_code == 404

        tenant = out["tenant_id"]

        async def owner() -> tuple[str, str]:
            async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
                p = str(
                    (
                        await c.execute(
                            text(
                                "SELECT principal_id FROM platform.tenant_role_assignment "
                                "WHERE role = 'OWNER' AND status = 'ACTIVE'"
                            )
                        )
                    ).scalar_one()
                )
                return p, await _session(c, tenant, p)

        p, sid = asyncio.run(owner())
        now = datetime.now(UTC)
        tok = issue_access_token(
            ring,
            issuer=ISS,
            audience=AUD,
            subject=p,
            tenant_id=tenant,
            client_id=out["console_client_id"],
            now=now,
            session_id=sid,
            auth_time=now - timedelta(seconds=5),
        )
        h = {"Authorization": f"Bearer {tok}"}
        me = api.get("/v1/tenant", headers=h).json()
        assert me["display_name"] == "Acme Widgets" and me["my_roles"][0]["status"] == "ACTIVE"
        r = api.post("/v1/tenant/members", json={"role": "MEMBER"}, headers=h)
        assert r.status_code == 201, r.text  # the new admin can invite their team straight away

        for i in range(4):  # the per-address bound (5 per window, one used above plus the clash)
            api.post("/v1/signup", json={"company_name": "x", "workspace": f"z{i}-{uid()[:6]}"})
        r = api.post("/v1/signup", json={"company_name": "x", "workspace": f"last-{uid()[:6]}"})
        assert r.status_code == 429
