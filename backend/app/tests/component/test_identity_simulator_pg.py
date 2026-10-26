"""Representative proofing end to end with the development simulators (ZID-VERIFY-001 §3-4, §9,
§22, §30 VERIFY-W2; ZID-ROOT-001 §19; ZID-OVERLAY-001 §4).

A company registers without development verification. Sign-up seeds the platform baseline
representative-proofing profile (IAL-Z2) and, in local/test, the simulated provider. The registrant
runs a check through the simulator, which answers through the public, signed provider-callback
path; the verified legal name is stored only as a commitment. Company verification then reads the
(simulated) Companies House record and makes the registrant OWNER only because their verified name
equals a current director's - a different verified name does not.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

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


def settings(rt_url: str, **kw: Any) -> Settings:
    return Settings(
        database_url=rt_url,
        token_issuer=ISS,
        token_audience=AUD,
        public_base_url=AUD,
        environment="test",
        cell_id="uk-1",
        tenant_console_redirect_uris=(CB,),
        tenant_signup_enabled=True,
        dev_simulators=True,
        **kw,
    )


def test_simulators_are_refused_outside_local(rt_url: str) -> None:
    with pytest.raises(RuntimeError):
        create_app(
            settings(rt_url).model_copy(update={"environment": "production", "token_issuer": None})
        )


def register(api: TestClient, number: str) -> dict[str, Any]:
    notice = api.get("/v1/signup/options").json()["notice_version"]
    r = api.post(
        "/v1/signup",
        json={
            "company_name": "Acme Widgets",
            "workspace": f"acme-{uid()[:6]}",
            "registration": {"namespace": "GB-COH", "identifier": number},
            "notice_version": notice,
        },
    )
    assert r.status_code == 201, r.text
    out: dict[str, Any] = r.json()
    assert out["organization"] == "VERIFICATION_REQUIRED"
    return out


def owner_token(ring: KeyRing, rt_url: str, owner_url: str, tenant: str) -> dict[str, str]:
    async def lookup() -> tuple[str, str, str]:
        async with engine(owner_url) as eng, eng.begin() as c:
            p = (
                await c.execute(
                    text(
                        "SELECT principal_id FROM platform.tenant_role_assignment "
                        "WHERE tenant_id = :t AND role = 'OWNER'"
                    ),
                    {"t": tenant},
                )
            ).scalar_one()
            client = (
                await c.execute(
                    text(
                        "SELECT console_client_id FROM platform.workspace_directory WHERE tenant_id = :t"
                    ),
                    {"t": tenant},
                )
            ).scalar_one()
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            return str(p), str(client), await _session(c, tenant, str(p))

    owner, client, sid = asyncio.run(lookup())
    now = datetime.now(UTC)
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
        scope=frozenset({"openid", "verification:read", "verification:execute"}),
    )
    return {"Authorization": f"Bearer {tok}", "X-Owner": owner}


def verify_identity(api: TestClient, h: dict[str, str], legal_name: str) -> str:
    owner = h["X-Owner"]
    headers = {"Authorization": h["Authorization"]}
    profiles = api.get("/verification-profiles", headers=headers).json()["items"]
    baseline = next(p for p in profiles if p["name"] == "representative-proofing")
    assert baseline["status"] == "ACTIVE" and baseline["level"] == "IAL-Z2"
    r = api.post(
        "/verifications",
        headers=headers,
        json={
            "profile_id": baseline["profile_id"],
            "subject_principal_id": owner,
            "purpose": "representative_proofing",
            "jurisdiction": "GB",
        },
    )
    assert r.status_code == 201 and r.json()["status"] == "PENDING_EXTERNAL", r.text
    assert r.json()["adapter_id"] == "zid-simulator"
    vid = r.json()["verification_id"]
    asks = api.get(f"/dev/verification-simulator/sessions/{vid}", headers=headers).json()
    assert asks["required_claims"] == ["legal_name", "birthdate"]
    r = api.post(
        f"/dev/verification-simulator/sessions/{vid}:complete",
        headers=headers,
        json={"legal_name": legal_name, "birthdate": "1980-02-01", "outcome": "VERIFIED"},
    )
    assert r.status_code == 200 and r.json()["outcome"] == "VERIFIED", r.text
    assert api.get(f"/verifications/{vid}", headers=headers).json()["status"] == "VERIFIED"
    return vid


def test_representative_proofing_then_company_verification(rt_url: str, owner_url: str) -> None:
    signup_api._recent.clear()
    signup_api._lookups.clear()
    ring = KeyRing(LocalSigner("s-sim"))
    app = create_app(settings(rt_url), keyring=ring)
    with TestClient(app) as api:
        assert api.get("/dev/verification-simulator/status").json()["enabled"] is True
        r = api.get("/v1/signup/registry/GB-COH/01234567")
        assert r.status_code == 200 and r.json()["legal_name"] == "ACME WIDGETS LIMITED"

        # --- the wrong person: verified, but not a director of this company -----------------
        other = register(api, "01234567")
        h = owner_token(ring, rt_url, owner_url, other["tenant_id"])
        verify_identity(api, h, "Somebody Else")
        r = api.post(
            "/v1/onboarding/organization",
            headers={"Authorization": h["Authorization"]},
            json={"namespace": "GB-COH", "identifier": "01234567"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["tenant_status"] == "ORGANIZATION_VERIFYING"  # not OWNER: no match
        assert r.json()["gates"][0]["status"] == "FAIL"

        # --- the director: identity verified, matched to the register, becomes OWNER ---------
        mine = register(api, "01234567")
        h = owner_token(ring, rt_url, owner_url, mine["tenant_id"])
        headers = {"Authorization": h["Authorization"]}
        declared = api.get("/v1/onboarding/organization", headers=headers).json()["declared"]
        assert declared["identifier"] == "01234567"
        verify_identity(api, h, "Jane Mary Doe")
        r = api.post(
            "/v1/onboarding/organization",
            headers=headers,
            json={"namespace": "GB-COH", "identifier": declared["identifier"]},
        )
        assert r.status_code == 200, r.text
        assert r.json()["legal_name"] == "ACME WIDGETS LIMITED"
        assert r.json()["tenant_status"] == "CONFIGURING", r.text
        roles = api.get("/v1/tenant", headers=headers).json()["my_roles"]
        assert any(x["role"] == "OWNER" and x["status"] == "ACTIVE" for x in roles)

    async def commitments_only() -> list[Any]:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(mine["tenant_id"])) as c:
            return list(
                (
                    await c.execute(
                        text(
                            "SELECT claim_type, assurance_level FROM verify.verification_assertion "
                            "WHERE status = 'VALID' ORDER BY claim_type"
                        )
                    )
                ).all()
            )

    rows = asyncio.run(commitments_only())
    assert [(r.claim_type, r.assurance_level) for r in rows] == [
        ("birthdate", "IAL-Z2"),
        ("legal_name", "IAL-Z2"),
    ]
