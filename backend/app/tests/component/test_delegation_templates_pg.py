"""Delegation workflow templates on a real database: defined once, applied into ordinary grants that keep every attenuation check."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from conftest import CELL, ctx, engine
from fastapi.testclient import TestClient
from onboarding_fixture import AUD, ISS, onboard
from sqlalchemy import text
from zid_db import tenant_transaction
from zid_platform.main import create_app
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
from zid_platform.settings import Settings

NOW = datetime.now(UTC)


def test_a_template_becomes_a_grant_within_the_applicants_own_authority(
    rt_url: str, owner_url: str
) -> None:
    o = onboard(rt_url, extra_actions=["payment.release"], extra_resources=["payment:*"])
    org, root = str(uuid.uuid4()), str(uuid.uuid4())

    async def seed() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(o.tenant)) as c:
            await create_organization(
                c,
                organization_id=org,
                tenant_id=o.tenant,
                legal_name="Acme",
                jurisdiction="GB",
                valid_from=NOW - timedelta(days=400),
            )
            await save_root(
                c,
                o.tenant,
                RootAssertion(
                    root_id=root,
                    organization_id=org,
                    holder_principal_id=o.owner,
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
        async with engine(owner_url) as eng, eng.begin() as c:
            await c.execute(
                text(
                    "INSERT INTO agent.writer_fence (tenant_id, cell_id, epoch) VALUES (:t, :c, 2) ON CONFLICT DO NOTHING"
                ),
                {"t": uuid.UUID(o.tenant), "c": uuid.UUID(CELL)},
            )

    asyncio.run(seed())
    app = create_app(
        Settings(
            database_url=rt_url,
            token_issuer=ISS,
            token_audience=AUD,
            public_base_url=AUD,
            environment="test",
            cell_id="uk-1",
            cell_uuid=CELL,
        ),
        keyring=o.ring,
    )
    h = o.bearer(o.owner)
    body = {
        "name": "Supplier payments up to 5,000",
        "actions": ["payment.release"],
        "resources": ["payment:*"],
        "value_amount": "5000",
        "value_currency": "GBP",
        "max_days": 30,
        "risk_class": 3,
    }
    with TestClient(app) as api:
        made = api.post("/v1/delegation-templates", json=body, headers=h)
        assert made.status_code == 201, made.text
        tid = made.json()["template_id"]
        assert (
            api.post(
                "/v1/delegation-templates",
                json={**body, "name": "SUPPLIER payments up to 5,000"},
                headers=h,
            ).status_code
            == 409
        )
        assert (
            api.post(
                "/v1/delegation-templates", json={**body, "value_currency": None}, headers=h
            ).status_code
            == 422
        )
        assert api.get("/v1/delegation-templates", headers=h).json()["items"][0]["value_limit"] == {
            "amount": "5000",
            "currency": "GBP",
        }

        applied = api.post(
            f"/v1/delegation-templates/{tid}:apply",
            json={
                "grantee_principal_id": o.second,
                "represented_principal_id": org,
                "authority_root_assertion_id": root,
                "days": 365,
            },
            headers=h,
        )
        assert applied.status_code == 201, applied.text
        grant = api.get(
            f"/v1/authority-grants/{applied.json()['authority_grant_id']}", headers=h
        ).json()
        assert (
            grant["actions"] == ["payment.release"]
            and float(grant["value_limit"]["amount"]) == 5000
            and grant["value_limit"]["currency"] == "GBP"
        )
        end = datetime.fromisoformat(grant["valid_to"])
        assert end - datetime.now(UTC) < timedelta(
            days=30, minutes=5
        )  # asking for more days than the template allows is capped

        assert api.post(f"/v1/delegation-templates/{tid}:retire", headers=h).status_code == 200
        gone = api.post(
            f"/v1/delegation-templates/{tid}:apply",
            json={"grantee_principal_id": o.second, "represented_principal_id": org},
            headers=h,
        )
        assert gone.status_code == 404
