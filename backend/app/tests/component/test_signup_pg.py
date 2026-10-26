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
            "branding": {"accent": "blue", "support_url": ""},  # presentation defaults (CR-022)
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


def test_signup_records_registration_notice_and_placement(
    rt_url: str, owner_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The declared register identifier, the acknowledged privacy-notice version and the data
    location are stored with the new tenant (tenant_registration), returned to the company
    verification step, and the register can be looked up first without storing anything."""
    from test_onboarding_organization_pg import registry_transport

    monkeypatch.setenv("ZID_SECRET__COMPANIES_HOUSE_API_KEY", "ch-test-key")
    signup_api._recent.clear()
    signup_api._lookups.clear()
    ring = KeyRing(LocalSigner("s-reg"))
    app = create_app(
        settings(rt_url, tenant_signup_enabled=True, tenant_signup_dev_verify=True), keyring=ring
    )
    app.state.registry_transport = registry_transport({})
    ws = f"acme-{uid()[:6]}"
    with TestClient(app) as api:
        opts = api.get("/v1/signup/options").json()
        notice = opts["notice_version"]
        assert notice and opts["registers"][0]["namespace"] == "GB-COH"
        assert opts["locations"][0]["cell_id"] == "uk-1" and opts["locations"][0]["this_cell"]

        r = api.get("/v1/signup/registry/GB-COH/01234567")
        assert r.status_code == 200 and r.json()["legal_name"] == "ACME WIDGETS LIMITED"
        assert "officers" not in r.json()  # register personal data never leaves the cell here
        assert api.get("/v1/signup/registry/GB-COH/09999999").status_code == 404

        body = {
            "company_name": "Acme Widgets",
            "workspace": ws,
            "registration": {"namespace": "GB-COH", "identifier": "01234567"},
            "notice_version": notice,
            "home_cell_id": "uk-1",
        }
        r = api.post("/v1/signup", json={**body, "notice_version": "1999-01-01"})
        assert r.status_code == 422 and r.json()["error"]["code"] == "NOTICE_VERSION_STALE"
        r = api.post("/v1/signup", json={**body, "home_cell_id": "eu-1"})
        assert r.status_code == 421 and r.json()["error"]["code"] == "CELL_ROUTING_REQUIRED"
        r = api.post(
            "/v1/signup",
            json={**body, "registration": {"namespace": "GB-COH", "identifier": "ABC"}},
        )
        assert r.status_code == 422
        r = api.post("/v1/signup", json=body)
        assert r.status_code == 201 and r.json()["registration_recorded"] is True, r.text
        tenant = r.json()["tenant_id"]

    async def stored() -> tuple[str, str, str]:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            row = (
                await c.execute(
                    text(
                        "SELECT register_identifier, notice_version, home_cell_id FROM platform.tenant_registration"
                    )
                )
            ).one()
            return row.register_identifier, row.notice_version, row.home_cell_id

    assert asyncio.run(stored()) == ("01234567", notice, "uk-1")

    async def owner_session() -> tuple[str, str]:
        async with engine(owner_url) as eng, eng.begin() as c:
            p = (
                await c.execute(
                    text(
                        "SELECT principal_id FROM platform.tenant_role_assignment WHERE tenant_id = :t AND role = 'OWNER'"
                    ),
                    {"t": tenant},
                )
            ).scalar_one()
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            return str(p), await _session(c, tenant, str(p))

    owner, sid = asyncio.run(owner_session())
    now = datetime.now(UTC)
    client = asyncio.run(_console_client(owner_url, tenant))
    tok = issue_access_token(
        ring,
        issuer=ISS,
        audience=AUD,
        subject=owner,
        tenant_id=tenant,
        client_id=client,
        now=now,
        session_id=sid,
        auth_time=now - timedelta(seconds=5),
    )
    with TestClient(app) as api:
        r = api.get("/v1/onboarding/organization", headers={"Authorization": f"Bearer {tok}"})
        assert r.status_code == 200, r.text
        d = r.json()["declared"]
        assert (
            d["identifier"] == "01234567"
            and d["notice_version"] == notice
            and d["home_cell_id"] == "uk-1"
        )
        r = api.get("/authn/recovery-codes/status", headers={"Authorization": f"Bearer {tok}"})
        assert r.status_code == 200 and r.json()["codes_remaining"] == 0


async def _console_client(owner_url: str, tenant: str) -> str:
    async with engine(owner_url) as eng, eng.begin() as c:
        return str(
            (
                await c.execute(
                    text(
                        "SELECT console_client_id FROM platform.workspace_directory WHERE tenant_id = :t"
                    ),
                    {"t": tenant},
                )
            ).scalar_one()
        )
