"""Organization gate of onboarding end to end (G-W1; OVERLAY-001 §4; ROOT-001 §4-6, §19).

The pending bootstrap OWNER submits a Companies House number. The root is established only when a
*current* director's registered name exactly equals the OWNER's verified legal-name claim; a company
where they are not an officer, a dissolved company, or a secretary role never yields a root, and a
register outage concludes nothing. On success the tenant is seeded (baseline release + one
PERSON_BOUND tenant-admin grant), the OWNER becomes ACTIVE and the tenant moves to CONFIGURING. The
worker's refresh later sees the director resign: the root is revoked and the grant stops working.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from conftest import ctx, engine
from fastapi.testclient import TestClient
from sqlalchemy import text
from zid_crypto import KeyRing, LocalSigner
from zid_db import tenant_transaction
from zid_platform.main import create_app
from zid_platform.modules.authority.ports import on_root_changed
from zid_platform.modules.organization.ports import adapter_for, refresh_registry_roots
from zid_platform.modules.principal.domain.principal import (
    Principal,
    PrincipalStatus,
    PrincipalType,
)
from zid_platform.modules.principal.infrastructure.principal_store import (
    create_principal,
    load_principal,
)
from zid_platform.modules.session.domain.oauth import OAuthClient
from zid_platform.modules.session.domain.session import Session
from zid_platform.modules.session.domain.session import SessionStatus as SStatus
from zid_platform.modules.session.infrastructure.oauth_store import create_client
from zid_platform.modules.session.infrastructure.session_store import create_session
from zid_platform.modules.session.ports import issue_access_token
from zid_platform.modules.tenant.application import lifecycle
from zid_platform.modules.verification.domain.verification import ClaimResult, SessionStatus
from zid_platform.modules.verification.infrastructure import verification_store as vstore
from zid_platform.settings import Settings

ISS, AUD = "https://id.zoiko.test", "https://api.zoiko.test"
CH = "https://api.company-information.service.gov.uk"


def uid() -> str:
    return str(uuid.uuid4())


def lei(base: str) -> str:
    digits = "".join(str(int(c, 36)) for c in base + "00")
    return f"{base}{98 - int(digits) % 97:02d}"


ACME_LEI = lei("5493001KJTIIGC8Y1R")
TODAY = datetime.now(UTC).date()
YEAR_AGO = (TODAY - timedelta(days=365)).isoformat()


def companies(resigned: bool = False) -> dict[str, tuple[dict[str, Any], list[dict[str, Any]]]]:
    return {
        "01234567": (
            {
                "company_name": "ACME WIDGETS LIMITED",
                "company_status": "active",
                "date_of_creation": "2001-02-03",
            },
            [
                {
                    "name": "DOE, Jane Mary",
                    "officer_role": "director",
                    "appointed_on": YEAR_AGO,
                    **({"resigned_on": TODAY.isoformat()} if resigned else {}),
                },
                {"name": "ROE, Richard", "officer_role": "secretary", "appointed_on": YEAR_AGO},
            ],
        ),
        "02222222": (
            {"company_name": "OTHER LTD", "company_status": "active"},
            [{"name": "BLOGGS, Joe", "officer_role": "director", "appointed_on": YEAR_AGO}],
        ),
        "03333333": (
            {"company_name": "GONE LTD", "company_status": "dissolved"},
            [{"name": "DOE, Jane Mary", "officer_role": "director", "appointed_on": YEAR_AGO}],
        ),
        "04444444": (
            {"company_name": "SECRETARIAL LTD", "company_status": "active"},
            [{"name": "DOE, Jane Mary", "officer_role": "secretary", "appointed_on": YEAR_AGO}],
        ),
    }


def registry_transport(state: dict[str, Any]) -> httpx.MockTransport:
    def handle(req: httpx.Request) -> httpx.Response:
        if state.get("down"):
            return httpx.Response(503)
        if req.url.host == "api.gleif.org":
            if req.url.path.endswith(ACME_LEI):
                body = {
                    "data": {
                        "attributes": {
                            "lei": ACME_LEI,
                            "entity": {
                                "legalName": {"name": "ACME WIDGETS LIMITED"},
                                "status": "ACTIVE",
                                "jurisdiction": "GB",
                            },
                            "registration": {"status": "ISSUED"},
                        }
                    }
                }
                return httpx.Response(200, json=body)
            return httpx.Response(404)
        assert req.url.host == "api.company-information.service.gov.uk"
        assert req.headers["authorization"].startswith("Basic ")  # the brokered credential
        parts = req.url.path.strip("/").split("/")
        company = companies(state.get("resigned", False)).get(parts[1])
        if company is None:
            return httpx.Response(404)
        if len(parts) == 2:
            return httpx.Response(200, json=company[0], headers={"etag": f"rev-{parts[1]}"})
        return httpx.Response(200, content=json.dumps({"items": company[1]}))

    return httpx.MockTransport(handle)


def test_organization_gate_registry_root_seeding_and_refresh(
    rt_url: str, owner_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ZID_SECRET__COMPANIES_HOUSE_API_KEY", "ch-test-key")
    signup, creator = uid(), uid()
    ring = KeyRing(LocalSigner("issuer-org"))
    sessions: dict[str, tuple[str, str, str]] = {}
    state: dict[str, Any] = {}
    out: dict[str, Any] = {}

    async def session_for(c: Any, tenant: str, p: str, cid: str) -> None:
        principal, _ = await load_principal(c, p)
        sid, now = uid(), datetime.now(UTC)
        await create_session(
            c,
            Session(
                sid,
                tenant,
                p,
                now - timedelta(seconds=5),
                "native:webauthn",
                2,
                principal.security_revision,
                1,
                SStatus.ACTIVE,
                now + timedelta(hours=1),
                now,
            ),
        )
        sessions[p] = (tenant, sid, cid)

    async def seed() -> None:
        async with engine(rt_url) as eng:
            async with tenant_transaction(eng, ctx(signup)) as c:
                await create_principal(
                    c,
                    Principal(creator, signup, PrincipalType.HUMAN, PrincipalStatus.ACTIVE, "uk-1"),
                )
                r = await lifecycle.bootstrap_tenant(
                    c,
                    creating_tenant_id=signup,
                    creator_principal_id=creator,
                    display_name="Acme",
                    tenant_slug=f"acme-{uid()[:6]}",
                    deployment_mode="O1",
                    home_cell_id="uk-1",
                    console=None,
                )
            tenant, owner = r.tenant.tenant_id, r.owner_principal_id
            out.update(tenant=tenant, owner=owner, other=uid(), client=f"console-{uid()[:8]}")
            async with tenant_transaction(eng, ctx(tenant)) as c:
                await create_client(
                    c,
                    OAuthClient(
                        out["client"],
                        tenant,
                        "PUBLIC",
                        "STANDARD",
                        ("https://app.example/cb",),
                        frozenset({"authorization_code"}),
                        frozenset({"openid"}),
                        (AUD,),
                        "none",
                        False,
                        "ACTIVE",
                        first_party=True,
                    ),
                )
                await session_for(c, tenant, owner, out["client"])
                await create_principal(
                    c,
                    Principal(
                        out["other"], tenant, PrincipalType.HUMAN, PrincipalStatus.ACTIVE, "uk-1"
                    ),
                )
                await session_for(c, tenant, out["other"], out["client"])
                pid, _ = await vstore.create_profile(
                    c,
                    tenant_id=tenant,
                    name=f"kyc-{uid()[:8]}",
                    level="IAL-Z2",
                    requirements={
                        "required_claims": ["legal_name"],
                        "accepted_evidence": ["GOVERNMENT_ID"],
                        "binding_required": True,
                        "jurisdictions": ["GB"],
                    },
                )
                profile = await vstore.load_profile(c, pid)
                now = datetime.now(UTC)
                s = vstore.VSession(
                    uid(),
                    tenant,
                    pid,
                    owner,
                    "onboarding",
                    "GB",
                    SessionStatus.VERIFIED,
                    "sim",
                    f"tx-{uid()[:8]}",
                    (),
                    "VERIFIED",
                    None,
                    1,
                    now,
                    now + timedelta(days=1),
                )
                await vstore.create_session(c, s)
                await vstore.insert_assertions(
                    c,
                    s,
                    profile,
                    (ClaimResult("legal_name", True, "Jane Mary Doe", "doc", ("GOVERNMENT_ID",)),),
                    adapter_id="sim",
                    reasons=("VERIFIED",),
                    evidence_commitment=None,
                    now=now,
                )

    asyncio.run(seed())
    tenant, owner, other = out["tenant"], out["owner"], out["other"]
    app = create_app(
        Settings(
            database_url=rt_url,
            token_issuer=ISS,
            token_audience=AUD,
            public_base_url=AUD,
            environment="test",
            cell_id="uk-1",
        ),
        keyring=ring,
    )
    app.state.registry_transport = registry_transport(state)

    def bearer(p: str) -> dict[str, str]:
        t, sid, cid = sessions[p]
        now = datetime.now(UTC)
        tok = issue_access_token(
            ring,
            issuer=ISS,
            audience=AUD,
            subject=p,
            tenant_id=t,
            client_id=cid,
            now=now,
            session_id=sid,
            auth_time=now - timedelta(seconds=5),
        )
        return {"Authorization": f"Bearer {tok}"}

    def submit(api: TestClient, number: str, who: str | None = None, **extra: Any) -> Any:
        body = {"namespace": "GB-COH", "identifier": number, **extra}
        return api.post("/v1/onboarding/organization", json=body, headers=bearer(who or owner))

    with TestClient(app) as api:
        r = submit(api, "01234567", other)
        assert r.status_code == 403 and r.json()["error"]["code"] == "TENANT_OWNER_UNAUTHORIZED"
        r = submit(api, "1234")  # not a company number shape
        assert r.status_code == 422

        # the OWNER is not an officer / the company is dissolved / a secretary is not a representative
        for number, reason in (
            ("02222222", "ORGANIZATION_ROLE_UNVERIFIED"),
            ("03333333", "ORGANIZATION_INACTIVE"),
            ("04444444", "ORGANIZATION_ROLE_UNVERIFIED"),
            ("09999999", "ORGANIZATION_NOT_FOUND"),
        ):
            r = submit(api, number)
            assert r.status_code == 200, r.text
            body = r.json()
            assert (
                body["tenant_status"] == "ORGANIZATION_VERIFYING" and body["admin_grant_id"] is None
            )
            (gate,) = body["gates"]
            assert gate["status"] == "FAIL" and gate["code"] == "TENANT_ORGANIZATION_UNVERIFIED"
            assert reason in gate["detail"]

        # an outage concludes nothing: retryable, no root
        state["down"] = True
        r = submit(api, "01234567")
        assert r.status_code == 503 and r.json()["error"]["code"] == "REGISTRY_UNAVAILABLE"
        state["down"] = False

        # a mismatched LEI corroboration is a conflict, never a silent pass
        r = submit(api, "01234567", lei=lei("5493009999999999ZZ"))
        assert r.status_code == 200 and r.json()["gates"][0]["status"] == "FAIL"

        g = api.get("/v1/onboarding/gates", headers=bearer(owner)).json()
        assert g["tenant_status"] == "ORGANIZATION_VERIFYING"
        assert g["gates"][0]["status"] == "PENDING"
        r = api.post("/v1/tenant/members", json={}, headers=bearer(owner))
        assert r.status_code == 403  # still no authority

        r = submit(api, "01234567", lei=ACME_LEI)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["tenant_status"] == "CONFIGURING" and body["gates"][0]["status"] == "PASS"
        assert body["legal_name"] == "ACME WIDGETS LIMITED" and body["admin_grant_id"]
        roles = api.get("/v1/tenant", headers=bearer(owner)).json()["my_roles"]
        assert [(a["role"], a["status"]) for a in roles] == [("OWNER", "ACTIVE")]
        g = api.get("/v1/onboarding/gates", headers=bearer(owner)).json()
        assert g["tenant_status"] == "CONFIGURING"
        assert {x["gate_id"]: x["status"] for x in g["gates"]} == {
            "tenant.owner": "PASS",
            "residency.placement": "PENDING",
            "tenant.domain": "PENDING",
        }
        # the seeded authority covers tenant administration, never business signing authority
        assert api.post("/v1/tenant/members", json={}, headers=bearer(owner)).status_code == 201
        r = submit(api, "01234567")
        assert r.status_code == 403  # the OWNER is no longer pending: the gate is closed

    async def facts_and_refresh() -> None:
        async with engine(owner_url) as eng:
            async with eng.begin() as c:
                outcomes = [
                    r.outcome
                    for r in (
                        await c.execute(
                            text(
                                "SELECT outcome FROM org.registry_observation WHERE tenant_id = :t "
                                "ORDER BY observed_at"
                            ),
                            {"t": uuid.UUID(tenant)},
                        )
                    ).all()
                ]
                assert outcomes == ["CORROBORATED", "ESTABLISHED"]
                root = (
                    await c.execute(
                        text(
                            "SELECT root_type, assurance_class, status, holder_principal_id "
                            "FROM org.authority_root_assertion WHERE tenant_id = :t"
                        ),
                        {"t": uuid.UUID(tenant)},
                    )
                ).one()
                assert (root.root_type, root.assurance_class, root.status) == (
                    "REGISTRY",
                    "AR3",
                    "ACTIVE",
                )
                assert str(root.holder_principal_id) == owner

            state["resigned"] = True
            transport = registry_transport(state)
            async with eng.begin() as c:
                done = await refresh_registry_roots(
                    c,
                    adapter_for=lambda ns: adapter_for(
                        ns, transport=transport, companies_house_key=lambda: "ch-test-key"
                    ),
                    now=datetime.now(UTC) + timedelta(hours=23),
                    on_root_changed=on_root_changed,
                )
                assert list(done.values()) == ["OFFICER_CEASED"]
            async with eng.begin() as c:
                status = (
                    await c.execute(
                        text(
                            "SELECT status FROM org.authority_root_assertion WHERE tenant_id = :t"
                        ),
                        {"t": uuid.UUID(tenant)},
                    )
                ).scalar_one()
                assert status == "REVOKED"
                grant = (
                    await c.execute(
                        text("SELECT status FROM authz.authority_grant WHERE tenant_id = :t"),
                        {"t": uuid.UUID(tenant)},
                    )
                ).scalar_one()
                assert grant != "ACTIVE"

    asyncio.run(facts_and_refresh())
    with TestClient(app) as api:
        r = api.post("/v1/tenant/members", json={}, headers=bearer(owner))
        assert r.status_code == 403  # the director left: the authority they held ended with it
