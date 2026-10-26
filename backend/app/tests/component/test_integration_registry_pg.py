"""Domain verification, application registry and connector framework (G-W2; OVERLAY-001 §5, §14-16,
§24; OVL-15, OVL-23, OVL-24).

- Domains: consumer domains can't be claimed; an unpublished or expired challenge fails; a
  published DNS TXT verifies; a second tenant can't capture a domain another tenant holds; lost
  control at re-verification makes it STALE (security revision bumped), never transferred.
- Applications: exact https endpoints, a shared secret only with a justification, known PEP
  obligations only, DRAFT -> TESTING -> ACTIVE (activation bumps the registry revision).
- Connectors: a SCIM connector over a provisioning source; SCIM traffic keeps it HEALTHY and fresh;
  credentials are write-only (fingerprints only, overlap during rotation); quarantine suspends the
  source so its SCIM writes stop at once, its facts stop being fresh, and release needs a second
  administrator; credential expiry raises 30/14/7/1-day alerts once each.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

from conftest import ctx, engine
from fastapi.testclient import TestClient
from onboarding_fixture import AUD, ISS, approval, onboard, uid
from sqlalchemy import text
from zid_db import tenant_transaction
from zid_platform.main import create_app
from zid_platform.modules.integration.application import service as integration
from zid_platform.modules.integration.ports import connector_fresh
from zid_platform.modules.principal.domain.principal import (
    Principal,
    PrincipalStatus,
    PrincipalType,
)
from zid_platform.modules.principal.infrastructure.principal_store import create_principal
from zid_platform.modules.tenant.application.domains import DomainChecks, reverify_due
from zid_platform.settings import Settings


def test_domains_applications_connectors(rt_url: str, owner_url: str) -> None:
    """OVL-22/23, CRYP-26: connector credentials rotate with a bounded overlap and consumers converge on the new one."""
    a = onboard(rt_url)
    b = onboard(rt_url)
    published: dict[str, list[str]] = {}

    async def txt(name: str) -> list[str]:
        return published.get(name, [])

    async def https(url: str) -> str | None:
        return None

    app = create_app(
        Settings(
            database_url=rt_url,
            token_issuer=ISS,
            token_audience=AUD,
            public_base_url=AUD,
            environment="test",
            cell_id="uk-1",
        ),
        keyring=a.ring,
    )
    app.state.domain_checks = DomainChecks(txt, https)
    # tenant B's tokens are signed by its own fixture ring: give the app a ring that has both keys
    b.ring = a.ring

    def code(r: Any) -> str:
        return str(r.json()["error"]["code"])

    domain = f"acme-{uid()[:8]}.example.com"
    with TestClient(app) as api:
        # --- domains ------------------------------------------------------------------------
        r = api.post("/v1/tenant/domains", json={"domain": "gmail.com"}, headers=a.bearer(a.owner))
        assert r.status_code == 422 and code(r) == "DOMAIN_NOT_CLAIMABLE"
        r = api.post("/v1/tenant/domains", json={"domain": "10.0.0.1"}, headers=a.bearer(a.owner))
        assert r.status_code == 422
        r = api.post(
            "/v1/tenant/domains", json={"domain": domain.upper()}, headers=a.bearer(a.owner)
        )
        assert r.status_code == 201, r.text
        d = r.json()
        assert d["domain"] == domain and d["status"] == "PENDING"
        pub = d["publish"]
        assert pub["record_type"] == "TXT" and pub["name"] == f"_zoiko-challenge.{domain}"
        r = api.post(f"/v1/tenant/domains/{d['domain_id']}:verify", headers=a.bearer(a.owner))
        assert r.status_code == 409 and code(r) == "DOMAIN_VERIFICATION_FAILED"
        published[pub["name"]] = ["unrelated", pub["value"]]
        r = api.post(f"/v1/tenant/domains/{d['domain_id']}:verify", headers=a.bearer(a.owner))
        assert r.status_code == 200 and r.json()["status"] == "VERIFIED", r.text

        gates = api.get("/v1/onboarding/gates", headers=a.bearer(a.owner)).json()["gates"]
        assert {g["gate_id"]: g["status"] for g in gates}["tenant.domain"] == "PASS"

        # tenant B proves control too, but A holds the domain: no first-come capture either way
        r = api.post("/v1/tenant/domains", json={"domain": domain}, headers=b.bearer(b.owner))
        assert r.status_code == 201
        db = r.json()
        published[db["publish"]["name"]] = [db["publish"]["value"]]
        r = api.post(f"/v1/tenant/domains/{db['domain_id']}:verify", headers=b.bearer(b.owner))
        assert r.status_code == 409 and code(r) == "DOMAIN_ALREADY_CLAIMED"

        # --- applications ----------------------------------------------------------------------
        base = {"name": "Payroll", "environment": "PROD", "protocols": ["OIDC", "AUTHZEN"]}
        r = api.post(
            "/v1/integration/applications",
            json={**base, "redirect_uris": ["http://payroll.example/cb"]},
            headers=a.bearer(a.owner),
        )
        assert r.status_code == 422  # plain http outside a DEV loopback
        r = api.post(
            "/v1/integration/applications",
            json={**base, "credential_profile": "secret_exception"},
            headers=a.bearer(a.owner),
        )
        assert r.status_code == 422  # a shared secret needs a recorded justification
        r = api.post(
            "/v1/integration/applications",
            json={**base, "pep_obligations": ["TELEPORT"]},
            headers=a.bearer(a.owner),
        )
        assert r.status_code == 422 and code(r) == "PEP_OBLIGATION_UNSUPPORTED"
        r = api.post(
            "/v1/integration/applications",
            json={
                **base,
                "redirect_uris": ["https://payroll.example/cb"],
                "resource_ids": ["https://api.payroll.example"],
                "pep_obligations": ["STEP_UP", "HUMAN_APPROVAL"],
                "pep_context_fields": ["purpose", "value"],
                "risk_floor": 2,
                "evidence_floor": 2,
            },
            headers=a.bearer(a.owner),
        )
        assert r.status_code == 201, r.text
        app_id = r.json()["application_id"]
        path = f"/v1/integration/applications/{app_id}:transition"
        r = api.post(path, json={"status": "ACTIVE"}, headers=a.bearer(a.owner))
        assert r.status_code == 409 and code(r) == "APPLICATION_STATE_INVALID"
        for status in ("TESTING", "ACTIVE"):
            r = api.post(path, json={"status": status}, headers=a.bearer(a.owner))
            assert r.status_code == 200 and r.json()["status"] == status, r.text
        assert (
            api.get("/v1/tenant", headers=a.bearer(a.owner)).json()["application_registry_revision"]
            == 1
        )

        # --- a SCIM connector over a provisioning source ---------------------------------------
        scim_client, svc = f"scim-{uid()[:8]}", uid()

        async def service_principal() -> None:
            async with engine(rt_url) as eng, tenant_transaction(eng, ctx(a.tenant)) as c:
                await create_principal(
                    c,
                    Principal(
                        svc, a.tenant, PrincipalType.APPLICATION, PrincipalStatus.ACTIVE, "uk-1"
                    ),
                )

        asyncio.run(service_principal())
        r = api.post(
            "/provisioning/sources",
            json={"client_id": scim_client, "display_name": "Workday"},
            headers=a.bearer(a.owner),
        )
        assert r.status_code == 201, r.text
        source_ref = f"provisioning_source:{r.json()['source_id']}"
        r = api.post(
            "/v1/integration/connectors",
            json={
                "connector_type": "SCIM",
                "display_name": "Workday SCIM",
                "capabilities": ["PROVISIONING", "GROUPS"],
                "source_of_truth_scope": ["membership", "manager"],
            },
            headers=a.bearer(a.owner),
        )
        assert r.status_code == 422  # a SCIM connector must reference its provisioning source
        r = api.post(
            "/v1/integration/connectors",
            json={
                "connector_type": "SCIM",
                "display_name": "Workday SCIM",
                "source_ref": source_ref,
                "capabilities": ["PROVISIONING", "GROUPS"],
                "source_of_truth_scope": ["membership", "manager"],
                "freshness_minutes": 30,
            },
            headers=a.bearer(a.owner),
        )
        assert r.status_code == 201, r.text
        cid = r.json()["connector_id"]

        scim = a.bearer(svc, scope="scim:provision", client=scim_client)
        user = {
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
            "userName": "jd",
            "externalId": "e1",
        }
        r = api.post("/scim/v2/Users", json=user, headers=scim)
        assert r.status_code == 201, r.text
        c = api.get(f"/v1/integration/connectors/{cid}", headers=a.bearer(a.owner)).json()
        assert c["health"] == "HEALTHY" and c["fresh_until"] and c["last_success_at"]

        # --- write-only credentials with a bounded overlap ------------------------------------------
        secret1, secret2 = "s1-" + uid(), "s2-" + uid()
        path = f"/v1/integration/connectors/{cid}/credential"
        r = api.put(
            path,
            json={
                "secret": secret1,
                "not_after": (datetime.now(UTC) + timedelta(days=10)).isoformat(),
            },
            headers=a.bearer(a.owner),
        )
        assert r.status_code == 200 and r.json() == {"connector_id": cid, "version": 1}
        r = api.put(path, json={"secret": secret2, "overlap_hours": 2}, headers=a.bearer(a.owner))
        assert r.json()["version"] == 2
        c = api.get(f"/v1/integration/connectors/{cid}", headers=a.bearer(a.owner)).json()
        assert [(x["version"], x["status"]) for x in c["credentials"]] == [
            (2, "ACTIVE"),
            (1, "RETIRING"),
        ]
        assert secret1 not in str(c) and secret2 not in str(c)

        async def values() -> list[str]:
            async with engine(rt_url) as eng, tenant_transaction(eng, ctx(a.tenant)) as conn:
                return await integration.credential_values(conn, a.tenant, cid)

        assert asyncio.run(values()) == [secret2, secret1]  # the old one only during the overlap

        async def values_after_overlap() -> list[str]:  # CRYP-26: 3h on, the 2h overlap has ended
            later = datetime.now(UTC) + timedelta(hours=3)
            async with engine(rt_url) as eng, tenant_transaction(eng, ctx(a.tenant)) as conn:
                return await integration.credential_values(conn, a.tenant, cid, now=later)

        assert asyncio.run(values_after_overlap()) == [secret2]  # consumers converge on the new one

        # --- quarantine: the source stops writing at once, its facts are no longer fresh --------
        r = api.post(
            f"/v1/integration/connectors/{cid}:quarantine",
            json={"reason_code": "CREDENTIAL_COMPROMISE"},
            headers=a.bearer(a.owner),
        )
        assert r.status_code == 200 and r.json()["health"] == "QUARANTINED", r.text
        r = api.post(
            "/scim/v2/Users", json={**user, "userName": "x2", "externalId": "e2"}, headers=scim
        )
        assert r.status_code == 403  # OVL-23: a compromised connector updates nothing

        async def fresh_now() -> bool:
            async with engine(rt_url) as eng, tenant_transaction(eng, ctx(a.tenant)) as conn:
                return await connector_fresh(conn, source_ref, datetime.now(UTC))

        assert not asyncio.run(fresh_now())
        assert asyncio.run(values()) == []  # quarantine revoked every credential
        r = api.put(path, json={"secret": "s3-" + uid()}, headers=a.bearer(a.owner))
        assert r.status_code == 409 and code(r) == "CONNECTOR_QUARANTINED"

        rid = approval(
            api,
            a,
            owner_url,
            action="integration.connector.manage",
            resource=f"integration_connector:{cid}",
            change="RELEASE",
        )
        r = api.post(
            f"/v1/integration/connectors/{cid}:release",
            json={"approval_request_id": rid},
            headers=a.bearer(a.owner),
        )
        assert r.status_code == 200 and r.json()["health"] == "DEGRADED", r.text
        r = api.post(
            "/scim/v2/Users", json={**user, "userName": "x3", "externalId": "e3"}, headers=scim
        )
        assert r.status_code == 201
        assert (
            api.get(f"/v1/integration/connectors/{cid}", headers=a.bearer(a.owner)).json()["health"]
            == "HEALTHY"
        )

        r = api.put(
            path,
            json={
                "secret": "s4-" + uid(),
                "not_after": (datetime.now(UTC) + timedelta(days=6)).isoformat(),
            },
            headers=a.bearer(a.owner),
        )
        assert r.status_code == 200

    async def worker() -> None:
        async with engine(owner_url) as eng:
            async with eng.begin() as conn:
                alerts = await integration.expiry_alerts(conn, datetime.now(UTC))
                assert (cid, 7) in alerts
            async with eng.begin() as conn:
                assert (cid, 7) not in await integration.expiry_alerts(conn, datetime.now(UTC))

            # domain control lost: STALE at re-verification, security revision bumped
            published.clear()
            async with eng.begin() as conn:
                out = await reverify_due(
                    conn,
                    checks=DomainChecks(txt, https),
                    now=datetime.now(UTC) + timedelta(days=31),
                )
                assert out[d["domain_id"]] == "STALE"
            async with eng.begin() as conn:
                row = (
                    await conn.execute(
                        text(
                            "SELECT d.status, t.security_revision FROM platform.tenant_domain d "
                            "JOIN platform.tenant t USING (tenant_id) WHERE d.domain_id = CAST(:id AS uuid)"
                        ),
                        {"id": d["domain_id"]},
                    )
                ).one()
                assert row.status == "STALE" and row.security_revision >= 2

    asyncio.run(worker())
