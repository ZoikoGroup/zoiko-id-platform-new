"""AuthZEN 1.0 evaluation endpoint end to end over HTTP (ZID-API-001 §9; AUTHZ-20 contract slice; INV-13).

The full app (zid_app shell + decision router) against PostgreSQL. Phase E authentication is simulated by
overriding the `authenticated_caller` dependency; without it every call is refused.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from conftest import CELL, ctx, engine
from fastapi.testclient import TestClient
from zid_context import RequestContext
from zid_db import tenant_transaction
from zid_platform.api_auth import Caller, authenticated_caller
from zid_platform.main import create_app
from zid_platform.modules.authority.domain.algebra import Authority, ValueLimit, Window, scope
from zid_platform.modules.authority.domain.grants import (
    ContinuityMode,
    Grant,
    GrantStatus,
    GrantType,
)
from zid_platform.modules.authority.ports import issue_grant
from zid_platform.modules.decision.domain.pdp import AuthnFact
from zid_platform.modules.organization.domain.roots import (
    Assurance,
    Fact,
    RootAssertion,
    RootStatus,
    RootType,
)
from zid_platform.modules.organization.infrastructure.root_store import (
    create_organization,
    save_root,
)
from zid_platform.modules.policy_authoring.infrastructure import release_store
from zid_platform.modules.policy_runtime.domain.release import (
    PolicyCase,
    PolicyRelease,
    ReleaseStatus,
)
from zid_platform.modules.principal.domain.principal import (
    Principal,
    PrincipalStatus,
    PrincipalType,
)
from zid_platform.modules.principal.infrastructure.principal_store import create_principal
from zid_platform.settings import Settings

NOW = datetime.now(UTC)
POLICY = (
    '@id("ap") permit (principal, action == Action::"invoice.approve", resource) '
    'when { context.authority_valid && context.purpose == "accounts_payable" };'
)
OK = {
    "tenant_match": True,
    "authority_valid": True,
    "authority_stale": False,
    "security_state": "NORMAL",
    "risk_class": 2,
    "purpose": "accounts_payable",
}


def uid() -> str:
    return str(uuid.uuid4())


def test_authzen_evaluation_over_http(rt_url: str) -> None:
    tenant, cfo, clerk, author, reviewer = uid(), uid(), uid(), uid(), uid()
    org = uid()

    async def seed() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            await create_principal(
                c, Principal(clerk, tenant, PrincipalType.HUMAN, PrincipalStatus.ACTIVE, "uk-1")
            )
            await create_organization(
                c,
                organization_id=org,
                tenant_id=tenant,
                legal_name="Acme",
                jurisdiction="GB",
                valid_from=NOW - timedelta(days=400),
            )
            root = uid()
            await save_root(
                c,
                tenant,
                RootAssertion(
                    root_id=root,
                    organization_id=org,
                    holder_principal_id=cfo,
                    root_type=RootType.REGISTRY,
                    facts=frozenset({Fact.ENTITY_EXISTENCE, Fact.OFFICIAL_ROLE}),
                    assurance=Assurance.AR3,
                    status=RootStatus.ACTIVE,
                    jurisdiction="GB",
                    valid_from=NOW - timedelta(days=100),
                    valid_to=None,
                    fresh_until=NOW + timedelta(days=1),
                ),
            )
            await issue_grant(
                c,
                Grant(
                    uid(),
                    tenant,
                    GrantType.ROLE_ISSUED,
                    cfo,
                    clerk,
                    org,
                    Authority(
                        actions=scope("invoice.approve"),
                        resources=scope("invoice:*"),
                        purposes=scope("accounts_payable"),
                        value=ValueLimit(Decimal(50_000), "GBP"),
                        window=Window(NOW - timedelta(days=1), NOW + timedelta(days=300)),
                    ),
                    ContinuityMode.ROLE_CONTINUOUS,
                    GrantStatus.ACTIVE,
                    NOW - timedelta(hours=1),
                    root_id=root,
                ),
                risk_level=2,
                now=NOW,
            )
            rid = uid()
            await release_store.create_release(
                c,
                PolicyRelease(
                    rid,
                    tenant,
                    POLICY,
                    "1",
                    ReleaseStatus.DRAFT,
                    (
                        PolicyCase("ok", "p", "invoice.approve", "r", OK, True),
                        PolicyCase(
                            "no authority",
                            "p",
                            "invoice.approve",
                            "r",
                            {**OK, "authority_valid": False},
                        ),
                    ),
                    author=author,
                ),
                policy_name="ap",
                version=1,
            )
            from zid_platform.modules.decision.api.authzen import catalog
            from zid_platform.modules.policy_authoring.infrastructure.release_store import move

            for target in (ReleaseStatus.VALIDATING, ReleaseStatus.REVIEW):
                await move(c, rid, target, catalog=catalog(), now=NOW)
            await release_store.add_reviewer(c, rid, reviewer)
            await move(c, rid, ReleaseStatus.ACTIVE, catalog=catalog(), now=NOW)

    asyncio.run(seed())
    app = create_app(Settings(database_url=rt_url))
    body = {
        "subject": {"type": "principal", "id": clerk},
        "action": {"name": "invoice.approve"},
        "resource": {"type": "invoice", "id": "7"},
        "context": {
            "purpose": "accounts_payable",
            "represented_principal_id": org,
            "value": {"amount": "120.50", "currency": "GBP"},
        },
    }
    with TestClient(app) as client:
        r = client.post("/access/v1/evaluation", json=body)  # no authenticated caller yet
        assert r.status_code == 401 and r.json()["error"]["code"] == "UNAUTHENTICATED"

        pep = RequestContext("req_t", "cor_t", "pep", CELL, tenant_id=tenant, principal_id=uid())
        app.dependency_overrides[authenticated_caller] = lambda: Caller(
            pep, AuthnFact(3, datetime.now(UTC) - timedelta(minutes=1)), security_state="NORMAL"
        )

        r = client.post("/access/v1/evaluation", json=body)
        assert r.status_code == 200, r.text
        out = r.json()
        assert out["decision"] is True and out["context"]["outcome"] == "ALLOW"
        assert {o["type"] for o in out["context"]["obligations"]} == {
            "decision_receipt",
            "commit_recheck",
        }
        assert out["context"]["policy_release_id"] and out["context"]["valid_until"].endswith("Z")

        over = {
            **body,
            "context": {**body["context"], "value": {"amount": "60000", "currency": "GBP"}},
        }
        r = client.post("/access/v1/evaluation", json=over)
        assert (
            r.json()["decision"] is False
            and "AUTHORITY_SCOPE_EXCEEDED" in r.json()["context"]["reason_codes"]
        )

        unknown = {**body, "action": {"name": "invent.something"}}
        r = client.post("/access/v1/evaluation", json=unknown)
        assert r.json() == {
            "decision": False,
            "context": {
                "decision_id": r.json()["context"]["decision_id"],
                "reason_codes": ["ACTION_UNKNOWN"],
            },
        }

        r = client.post(
            "/access/v1/evaluation", json={**body, "tenant_id": uid()}
        )  # INV-13: never from the body
        assert r.status_code == 422

        r = client.post(
            "/access/v1/evaluation",
            json={
                **body,
                "context": {**body["context"], "value": {"amount": "NaN", "currency": "GBP"}},
            },
        )
        assert r.status_code == 422  # non-finite decimal refused
