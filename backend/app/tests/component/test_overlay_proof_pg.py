"""The overlay proof (ZID-VERIFY-MASTER-001 AT-01, P0-01, BG-09; DEC-A1; ZID-OVERLAY-001).

"An external IdP user and an agent are authorized by Zoiko without directory migration; deprovisioning
revokes them." One composed story over HTTP against PostgreSQL with row-level security in force:

  1. An HRIS connector (Workday-like) provisions a person through SCIM. Nobody is migrated: Zoiko holds an
     identifier and a membership, no credentials and no directory.
  2. The person signs in through the company's OWN identity provider (OIDC, RS256 ID token, private_key_jwt
     client authentication). Zoiko stores no password and no passkey for them.
  3. A root holder grants the person narrow authority; the person delegates a smaller slice to an AI agent.
  4. A policy enforcement point asks Zoiko whether the person, and the agent, may approve an invoice. Zoiko
     answers ALLOW inside the granted limits, DENY outside them, and tells the PEP to keep a receipt.
  5. The HRIS reports the person has left. Their authority is revoked, the agent's delegated authority goes
     with it, the next decisions are DENY, and the IdP login no longer works: no identity migration to undo.
  6. Every decision, allow and deny, left a minimized receipt that outlives the person's access.

The upstream IdP is the same signing fake the federation suite uses; everything else is the real code path.
"""

from __future__ import annotations

import asyncio
import dataclasses
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
from conftest import CELL, ctx, engine
from fastapi.testclient import TestClient
from onboarding_fixture import AUD, ISS, onboard
from sqlalchemy import text
from test_federation_oidc_pg import COOKIE, IDP, ORIGIN, FakeIdP
from zid_db import tenant_transaction
from zid_platform.main import create_app
from zid_platform.modules.authority.domain.algebra import Authority, ValueLimit, Window, scope
from zid_platform.modules.authority.domain.grants import (
    ContinuityMode,
    Grant,
    GrantStatus,
    GrantType,
)
from zid_platform.modules.authority.ports import issue_grant
from zid_platform.modules.mandate.domain.ledger import Budget, MandateStatus
from zid_platform.modules.mandate.domain.subagent import MandateTerms
from zid_platform.modules.mandate.infrastructure.ledger_store import MandateRecord, create_mandate
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
from zid_platform.modules.session.domain.session import Session, SessionStatus
from zid_platform.modules.session.infrastructure.oauth_store import create_client
from zid_platform.modules.session.infrastructure.session_store import create_session, issue_handle
from zid_platform.settings import Settings

NOW = datetime.now(UTC)
YEAR = Window(NOW - timedelta(days=1), NOW + timedelta(days=300))


def uid() -> str:
    return str(uuid.uuid4())


def test_overlay_proof_external_idp_user_and_agent_without_directory_migration(
    rt_url: str, owner_url: str
) -> None:
    """P0-01 overlay proof (OVL-01), plus MACH-24 (the agent's receipt links its mandate) and PRIV-28 (an agent
    cannot widen its own purpose)."""
    o = onboard(rt_url)
    tenant = o.tenant
    cfo, svc, agent = uid(), uid(), uid()
    web, hris_client = f"web-{uid()}", f"hris-{uid()[:8]}"
    idp = FakeIdP(o.ring, "zoiko-at-acme")

    async def seed_actors() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            await create_principal(
                c, Principal(cfo, tenant, PrincipalType.HUMAN, PrincipalStatus.ACTIVE, "uk-1")
            )
            await create_principal(
                c, Principal(svc, tenant, PrincipalType.APPLICATION, PrincipalStatus.ACTIVE, "uk-1")
            )
            await create_client(
                c,
                OAuthClient(
                    web,
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

    asyncio.run(seed_actors())
    app = create_app(
        Settings(
            database_url=rt_url,
            token_issuer=ISS,
            token_audience=AUD,
            public_base_url=AUD,
            environment="test",
            cell_id="uk-1",
            webauthn_rp_id="id.zoiko.test",
            webauthn_origins=(ORIGIN,),
        ),
        keyring=o.ring,
    )
    app.state.federation_settings = dataclasses.replace(
        app.state.federation_settings, transport=httpx.MockTransport(idp.handler)
    )

    async def seed_authority(person: str) -> tuple[str, str]:
        """cfo (root holder) -> person -> agent: each hop narrower than the one before."""
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            org, root = uid(), uid()
            await create_organization(
                c,
                organization_id=org,
                tenant_id=tenant,
                legal_name="Acme Ltd",
                jurisdiction="GB",
                valid_from=NOW - timedelta(days=400),
            )
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
            await create_principal(
                c,
                Principal(
                    agent,
                    tenant,
                    PrincipalType.AI_AGENT,
                    PrincipalStatus.ACTIVE,
                    "uk-1",
                    sponsor_principal_id=person,
                ),
            )

            def authority(limit: int) -> Authority:
                return Authority(
                    actions=scope("invoice.approve"),
                    resources=scope("invoice:*"),
                    purposes=scope("accounts_payable"),
                    value=ValueLimit(Decimal(limit), "GBP"),
                    window=YEAR,
                )

            top = Grant(
                uid(),
                tenant,
                GrantType.ROLE_ISSUED,
                cfo,
                person,
                org,
                authority(50_000),
                ContinuityMode.ROLE_CONTINUOUS,
                GrantStatus.ACTIVE,
                NOW - timedelta(hours=1),
                root_id=root,
                delegation_allowed=True,
                max_delegation_depth=2,
            )
            await issue_grant(c, top, risk_level=2, now=NOW)
            child = Grant(
                uid(),
                tenant,
                GrantType.AGENT_DELEGATION,
                person,
                agent,
                org,
                authority(5_000),
                ContinuityMode.PERSON_BOUND,
                GrantStatus.ACTIVE,
                NOW - timedelta(hours=1),
                parent_grant_id=top.grant_id,
                delegation_allowed=False,
                max_delegation_depth=1,
            )
            await issue_grant(c, child, risk_level=2, now=NOW)
            mandate = uuid.uuid4()
            terms = MandateTerms(
                mandate_id=str(mandate),
                executor_principal_id=agent,
                represented_principal_id=org,
                purpose_id=uid(),
                actions=frozenset({"invoice.approve"}),
                resource_scope=frozenset({"invoice:*"}),
                tools=frozenset({"erp"}),
                jurisdictions=frozenset({"GB"}),
                valid_from=NOW - timedelta(hours=1),
                valid_to=NOW + timedelta(days=30),
                delegation_allowed=False,
                max_delegation_depth=0,
                status=MandateStatus.ACTIVE,
            )
            await create_mandate(
                c,
                MandateRecord(
                    mandate_id=mandate,
                    tenant_id=uuid.UUID(tenant),
                    authority_grant_id=uuid.UUID(child.grant_id),
                    represented_principal_id=uuid.UUID(org),
                    executor_principal_id=uuid.UUID(agent),
                    purpose_id=uuid.UUID(terms.purpose_id),
                    resource_scope_id=uuid.uuid4(),
                    allowed_actions=("invoice.approve",),
                    budget=Budget(currency="GBP", value_limit=Decimal(5000)),
                    home_cell_id=uuid.UUID(CELL),
                    status=MandateStatus.ACTIVE,
                    valid_from=terms.valid_from,
                    valid_to=terms.valid_to,
                    created_by_principal_id=uuid.UUID(person),
                ),
                terms=terms,
            )
            return org, str(mandate)

    async def fresh_session_handle(person: str) -> str:
        sid, now = uid(), datetime.now(UTC)
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            principal, _ = await load_principal(c, person)
            await create_session(
                c,
                Session(
                    sid,
                    tenant,
                    person,
                    now - timedelta(seconds=20),
                    "native:webauthn",
                    2,
                    principal.security_revision,
                    1,
                    SessionStatus.ACTIVE,
                    now + timedelta(hours=8),
                    now,
                ),
            )
            return await issue_handle(c, sid)

    async def person_status(person: str) -> PrincipalStatus:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            return (await load_principal(c, person))[0].status

    async def receipts(decision_ids: list[str]) -> dict[str, str]:
        async with engine(owner_url) as eng, eng.connect() as c:
            rows = (
                await c.execute(
                    text(
                        "SELECT decision_id::text AS d, outcome FROM evidence.decision_receipt "
                        "WHERE tenant_id = :t AND decision_id = ANY(:ids)"
                    ),
                    {"t": uuid.UUID(tenant), "ids": [uuid.UUID(d) for d in decision_ids]},
                )
            ).all()
        return {r.d: r.outcome for r in rows}

    with TestClient(app, base_url=ISS, follow_redirects=False) as api:
        owner = o.bearer(o.owner)  # the company administrator; also the PEP's service credential
        hris = o.bearer(svc, scope="scim:provision", client=hris_client)

        # --- 1. the HRIS provisions the person: an identifier and a membership, nothing migrated
        assert (
            api.post(
                "/provisioning/sources",
                json={"client_id": hris_client, "display_name": "Workday"},
                headers=owner,
            ).status_code
            == 201
        )
        r = api.post(
            "/scim/v2/Users",
            json={
                "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
                "userName": "jane.doe",
                "externalId": "w-1001",
            },
            headers=hris,
        )
        assert r.status_code == 201, r.text
        user = r.json()["id"]
        joiner = api.post(
            "/provisioning/lifecycle-events",
            json={"user_id": user, "event": "JOINER"},
            headers=hris,
        ).json()
        person = joiner["principal_id"]
        assert (
            joiner["membership"] == "CREATED"
        )  # a plain MEMBER: provisioning never grants authority

        org, mandate = asyncio.run(seed_authority(person))

        # --- 2. the company's own IdP authenticates them: explicit link, then ordinary logins
        spec = {
            "protocol": "OIDC",
            "display_name": "Acme Entra",
            "issuer": IDP,
            "client_id": "zoiko-at-acme",
            "client_auth": "private_key_jwt",
            "authorization_endpoint": IDP + "/authorize",
            "token_endpoint": IDP + "/token",
            "jwks": idp.jwks(),
            "algorithms": ["RS256"],
            "acr_levels": {"phr": 2},
            "metadata_expires_at": (datetime.now(UTC) + timedelta(days=30)).isoformat(),
        }
        r = api.post("/federation/integrations", headers=owner, json=spec)
        assert r.status_code == 201, r.text
        conn = r.json()["connection_id"]

        def start() -> str:
            r = api.get(f"/federation/connections/{conn}/login", params={"client_id": web})
            assert r.status_code == 303, r.text
            return idp.authorize(r.headers["location"])

        handle = asyncio.run(fresh_session_handle(person))
        r = api.post(
            f"/federation/connections/{conn}/link",
            json={"client_id": web},
            headers={"Origin": ORIGIN, "Cookie": f"{COOKIE}={handle}"},
        )
        assert r.status_code == 200, r.text
        linked = api.get(idp.authorize(r.json()["authorization_url"]))
        assert linked.status_code == 200 and linked.json()["principal_id"] == person
        login = api.get(start())
        assert login.status_code == 200, login.text
        assert login.json()["principal_id"] == person and login.json()["aal"] == 2
        assert login.json()["authentication_context"] == f"oidc:{IDP}"  # authenticated by THEIR IdP

        # --- 3/4. the PEP asks: may the person, and the agent, approve this invoice?
        def ask(
            subject: str,
            amount: str,
            mandate_id: str | None = None,
            purpose: str = "accounts_payable",
        ) -> dict[str, Any]:
            body = {
                "subject": {"type": "principal", "id": subject},
                "action": {"name": "invoice.approve"},
                "resource": {"type": "invoice", "id": "7"},
                "context": {
                    "purpose": purpose,
                    "represented_principal_id": org,
                    "value": {"amount": amount, "currency": "GBP"},
                    **({"mandate_id": mandate_id} if mandate_id else {}),
                },
            }
            r = api.post("/access/v1/evaluation", json=body, headers=owner)
            assert r.status_code == 200, r.text
            return dict(r.json())

        person_ok = ask(person, "120.50")
        assert person_ok["decision"] is True, person_ok
        assert {x["type"] for x in person_ok["context"]["obligations"]} >= {
            "decision_receipt",
            "commit_recheck",
        }
        no_mandate = ask(agent, "120.50")  # a grant alone gives an agent no implicit authority
        assert no_mandate["decision"] is False
        assert "MANDATE_MISSING" in no_mandate["context"]["reason_codes"]
        agent_ok = ask(agent, "120.50", mandate)
        assert agent_ok["decision"] is True, (
            agent_ok
        )  # authority delegated by the person, inside her own
        # PRIV-28: the agent names a broader purpose than the one its authority was given for; it is not allowed
        # to widen its own purpose to reach more personal data
        wider = ask(agent, "120.50", mandate, purpose="marketing")
        assert wider["decision"] is False, wider
        person_over = ask(person, "60000")  # above her 50k grant
        assert (
            person_over["decision"] is False
            and "AUTHORITY_SCOPE_EXCEEDED" in person_over["context"]["reason_codes"]
        )
        agent_over = ask(
            agent, "6000", mandate
        )  # inside the person's limit but above what she gave the agent
        assert (
            agent_over["decision"] is False
            and "AUTHORITY_SCOPE_EXCEEDED" in agent_over["context"]["reason_codes"]
        )
        before = [
            d["context"]["decision_id"]
            for d in (person_ok, agent_ok, person_over, agent_over, no_mandate)
        ]

        # MACH-24: the agent's receipt names its mandate and the person it acted for, by handle and commitment
        async def agent_receipt() -> dict[str, Any]:
            async with engine(owner_url) as eng, eng.connect() as c:
                row = await c.execute(
                    text("SELECT body FROM evidence.decision_receipt WHERE decision_id = :d"),
                    {"d": uuid.UUID(agent_ok["context"]["decision_id"])},
                )
                return dict(row.scalar_one())

        rb = asyncio.run(agent_receipt())
        assert rb["mandate_commitment"] == str(mandate)
        assert rb["actor_handle"] and rb["represented_handle"] != rb["actor_handle"]
        assert agent not in str(rb) and person not in str(rb)  # handles, never the principal ids

        # --- 5. the HRIS reports the leaver: authority, the agent's delegation and the login all end
        left = api.post(
            "/provisioning/lifecycle-events",
            json={"user_id": user, "event": "LEAVER"},
            headers=hris,
        )
        assert left.status_code == 200 and left.json()["grants_revoked"] >= 1
        assert asyncio.run(person_status(person)) is PrincipalStatus.SUSPENDED

        person_after, agent_after = ask(person, "120.50"), ask(agent, "120.50", mandate)
        assert person_after["decision"] is False
        assert agent_after["decision"] is False  # the delegation went with the person's authority
        assert person_after["context"]["reason_codes"] and agent_after["context"]["reason_codes"]
        relogin = api.get(start())
        assert relogin.status_code in (401, 403), (
            relogin.text
        )  # the IdP cannot sign a leaver back in

        # --- 6. every decision left a receipt, and the earlier ones outlive the person's access
        after = [d["context"]["decision_id"] for d in (person_after, agent_after)]
        kept = asyncio.run(receipts(before + after))
        assert set(kept) == set(before + after), "a decision left no receipt"
        assert kept[before[0]] == "ALLOW" and kept[before[1]] == "ALLOW"
        assert all(kept[d] == "DENY" for d in (before[2], before[3], before[4], *after))
