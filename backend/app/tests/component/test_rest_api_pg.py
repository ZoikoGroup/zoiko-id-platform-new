"""Authority, mandate and approval REST APIs over HTTP (ZID-API-001 §10-11; MAND-06/08/16/17/21; INV-13).

Full app against PostgreSQL with RLS in force; Phase E authentication is simulated by overriding
`authenticated_caller` with the principal making each call.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from admin_fixture import make_admin
from conftest import CELL, ctx, engine
from fastapi.testclient import TestClient
from sqlalchemy import text
from zid_context import RequestContext
from zid_db import tenant_transaction
from zid_platform.api_auth import Caller, authenticated_caller
from zid_platform.main import create_app
from zid_platform.modules.decision.domain.pdp import AuthnFact
from zid_platform.modules.mandate.domain.ledger import Budget, MandateStatus
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
from zid_platform.modules.principal.infrastructure.principal_store import create_principal
from zid_platform.settings import Settings

NOW = datetime.now(UTC)


def uid() -> str:
    return str(uuid.uuid4())


def test_grant_mandate_and_approval_apis(rt_url: str, owner_url: str) -> None:
    tenant, cfo, clerk, approver, agent, org, root = uid(), uid(), uid(), uid(), uid(), uid(), uid()
    adm = uid()

    async def seed() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            for p in (cfo, clerk, approver, agent, adm):
                await create_principal(
                    c, Principal(p, tenant, PrincipalType.HUMAN, PrincipalStatus.ACTIVE, "uk-1")
                )
            await make_admin(c, tenant, adm, ["mandate.manage"], ["mandate:*"])
            await create_organization(
                c,
                organization_id=org,
                tenant_id=tenant,
                legal_name="Acme",
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
        async with engine(owner_url) as eng, eng.begin() as c:
            await c.execute(
                text(
                    "INSERT INTO agent.writer_fence (tenant_id, cell_id, epoch) VALUES (:t, :c, 2)"
                ),
                {"t": uuid.UUID(tenant), "c": uuid.UUID(CELL)},
            )

    asyncio.run(seed())
    app = create_app(Settings(database_url=rt_url, cell_uuid=CELL))

    def act_as(principal: str) -> None:
        rc = RequestContext(
            f"req_{principal[:8]}", "cor_t", "api", CELL, tenant_id=tenant, principal_id=principal
        )
        app.dependency_overrides[authenticated_caller] = lambda: Caller(
            rc,
            AuthnFact(3, datetime.now(UTC) - timedelta(minutes=1)),
            security_state="NORMAL",
            client_id="c1",
        )

    window = {
        "valid_from": (NOW - timedelta(hours=1)).isoformat(),
        "valid_to": (NOW + timedelta(days=30)).isoformat(),
    }
    grant_body = {
        "grantee_principal_id": clerk,
        "represented_principal_id": org,
        "authority_root_assertion_id": root,
        "actions": ["invoice.approve"],
        "resources": ["invoice:*"],
        "value_limit": {"amount": "50000", "currency": "GBP"},
        "risk_class": 2,
        **window,
    }

    with TestClient(app) as client:
        assert (
            client.post(
                "/v1/authority-grants", json=grant_body, headers={"Idempotency-Key": "k-1"}
            ).status_code
            == 401
        )

        # --- grants: the caller is the grantor; only a root holder can issue a top grant
        act_as(clerk)
        r = client.post("/v1/authority-grants", json=grant_body, headers={"Idempotency-Key": "k-0"})
        assert r.status_code == 403, r.text
        act_as(cfo)
        r = client.post(
            "/v1/authority-grants",
            json={**grant_body, "is_admin": True},
            headers={"Idempotency-Key": "k-x"},
        )
        assert r.status_code == 422  # unknown fields are refused, no self-elevation shortcuts
        r = client.post("/v1/authority-grants", json=grant_body, headers={"Idempotency-Key": "k-1"})
        assert r.status_code == 201, r.text
        grant = r.json()
        assert grant["grantor_principal_id"] == cfo and grant["status"] == "ACTIVE"
        again = client.post(
            "/v1/authority-grants", json=grant_body, headers={"Idempotency-Key": "k-1"}
        )
        assert (
            again.json()["authority_grant_id"] == grant["authority_grant_id"]
        )  # replayed, not re-issued
        changed = client.post(
            "/v1/authority-grants",
            json={**grant_body, "actions": ["x"]},
            headers={"Idempotency-Key": "k-1"},
        )
        assert (
            changed.status_code == 409
            and changed.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
        )
        gid = grant["authority_grant_id"]
        assert client.get(f"/v1/authority-grants/{gid}").json()["actions"] == ["invoice.approve"]

        # the issue is also an append-only audit record: who, whom, and when, written once (the replay above did not write a second)
        async def audited(event_type: str) -> list[Any]:
            async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
                return list(
                    (
                        await c.execute(
                            text(
                                "SELECT actor_type, actor_handle, body FROM evidence.audit_record "
                                "WHERE event_type = :e AND target_ref = :g"
                            ),
                            {"e": event_type, "g": gid},
                        )
                    ).mappings()
                )

        issued = asyncio.run(audited("authority.grant.issued"))
        assert len(issued) == 1 and issued[0]["actor_type"] == "HUMAN" and issued[0]["actor_handle"]
        details = issued[0]["body"]["details"]
        assert details["grantee_principal_id"] == clerk and details["actions"] == 1
        assert issued[0]["body"]["outcome"] == "SUCCESS"
        chain = client.get(f"/v1/authority-grants/{gid}/chain", params={"risk_class": 2}).json()
        assert (
            chain["valid"]
            and chain["chain_ids"] == [gid]
            and chain["authority_root_assertion_id"] == root
        )
        assert client.get(f"/v1/authority-grants/{uid()}").status_code == 404
        assert client.get("/v1/authority-grants/not-a-uuid").status_code == 422

        # --- approvals: approver acts under their own, server-resolved grant
        r = client.post(
            "/v1/authority-grants",
            json={**grant_body, "grantee_principal_id": approver},
            headers={"Idempotency-Key": "k-2"},
        )
        approver_grant = r.json()["authority_grant_id"]
        act_as(clerk)
        tx = "payment:pay_1|GBP|1200.00|acct_9"
        r = client.post(
            "/v1/approval-requests",
            json={
                "subject_ref": "payment:pay_1",
                "transaction_context": tx,
                "roles": ["invoice.approve"],
            },
        )
        assert r.status_code == 201, r.text
        req_id = r.json()["approval_request_id"]
        decision = {
            "verdict": "APPROVE",
            "authority_grant_id": approver_grant,
            "transaction_context": tx,
            "risk_class": 2,
        }
        r = client.post(f"/v1/approval-requests/{req_id}/decisions", json=decision)
        assert r.status_code == 403 and r.json()["error"]["code"] == "APPROVER_GRANT_NOT_HELD"
        act_as(approver)
        r = client.post(f"/v1/approval-requests/{req_id}/decisions", json=decision)
        assert r.status_code == 201, r.text
        assert r.json()["state"] == "APPROVED" and r.json()["authority_valid"]
        assert (
            client.post(f"/v1/approval-requests/{req_id}/decisions", json=decision).status_code
            == 409
        )
        state = client.get(
            f"/v1/approval-requests/{req_id}", params={"transaction_context": tx}
        ).json()
        assert state["state"] == "APPROVED"
        changed_tx = client.get(
            f"/v1/approval-requests/{req_id}",
            params={"transaction_context": tx.replace("1200.00", "9999.00")},
        ).json()
        assert changed_tx["state"] != "APPROVED"  # a changed transaction makes the approval stale

        # --- mandates: executor-only, inside terms, fenced ledger writes
        mid = uuid.uuid4()

        async def new_mandate() -> None:
            async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
                await create_mandate(
                    c,
                    MandateRecord(
                        mandate_id=mid,
                        tenant_id=uuid.UUID(tenant),
                        authority_grant_id=uuid.UUID(gid),
                        represented_principal_id=uuid.UUID(org),
                        executor_principal_id=uuid.UUID(agent),
                        purpose_id=uuid.uuid4(),
                        resource_scope_id=uuid.uuid4(),
                        allowed_actions=("invoice.approve",),
                        budget=Budget(currency="GBP", value_limit=Decimal(1000)),
                        home_cell_id=uuid.UUID(CELL),
                        status=MandateStatus.ACTIVE,
                        valid_from=NOW - timedelta(hours=1),
                        valid_to=NOW + timedelta(days=1),
                        created_by_principal_id=uuid.UUID(cfo),
                    ),
                )

        asyncio.run(new_mandate())
        reserve = {
            "action": "invoice.approve",
            "transaction_context": "inv-7",
            "value": {"amount": "400", "currency": "GBP"},
        }
        path = f"/v1/mandates/{mid}/reservations"
        act_as(clerk)
        r = client.post(path, json=reserve, headers={"Idempotency-Key": "res-0001"})
        assert r.status_code == 403 and r.json()["error"]["code"] == "MANDATE_EXECUTOR_MISMATCH"
        act_as(agent)
        assert client.post(path, json=reserve).status_code == 422  # Idempotency-Key is required
        r = client.post(
            path, json={**reserve, "action": "invoice.pay"}, headers={"Idempotency-Key": "res-0002"}
        )
        assert r.json()["error"]["code"] == "MANDATE_SCOPE_EXCEEDED"
        r = client.post(
            path, json={**reserve, "tool": "bank"}, headers={"Idempotency-Key": "res-0003"}
        )
        assert r.json()["error"]["code"] == "MANDATE_TOOL_NOT_ALLOWED"
        r = client.post(path, json=reserve, headers={"Idempotency-Key": "res-0001"})
        assert r.status_code == 201, r.text
        res1 = r.json()
        assert (
            client.post(path, json=reserve, headers={"Idempotency-Key": "res-0001"}).json()[
                "reservation_id"
            ]
            == res1["reservation_id"]
        )
        res2 = client.post(path, json=reserve, headers={"Idempotency-Key": "res-0004"}).json()
        over = client.post(path, json=reserve, headers={"Idempotency-Key": "res-0005"})
        assert over.status_code == 409, over.text  # 400 + 400 + 400 > 1000
        r = client.post(
            f"/v1/mandate-reservations/{res1['reservation_id']}:commit",
            json={"recheck_passed": True},
        )
        assert r.status_code == 200 and r.json()["status"] == "COMMITTED", r.text
        assert (
            client.post(f"/v1/mandate-reservations/{res2['reservation_id']}:release").json()[
                "status"
            ]
            == "RELEASED"
        )
        bal = client.get(f"/v1/mandates/{mid}/balance").json()
        assert Decimal(bal["committed"]["value"]) == 400 and Decimal(bal["reserved"]["value"]) == 0
        assert Decimal(str(bal["remaining"]["value"])) == 600

        # --- roles (CR-039): a bystander changes nothing, the executor may only pause, and each sees their own
        act_as(clerk)
        assert client.post(f"/v1/mandates/{mid}:pause").status_code == 403
        assert client.get("/v1/mandates/mine").json()["items"] == []
        act_as(agent)
        assert client.post(f"/v1/mandates/{mid}:revoke").status_code == 403
        assert client.post(f"/v1/mandates/{mid}:activate").status_code == 403
        mine = client.get("/v1/mandates/mine").json()["items"]
        assert [(m["mandate_id"], m["i_am"], m["my_moves"]) for m in mine] == [
            (str(mid), ["executor"], ["pause"])
        ]
        act_as(cfo)
        mine = client.get("/v1/mandates/mine").json()["items"]
        assert [(m["i_am"], m["my_moves"]) for m in mine] == [(["creator"], ["pause", "revoke"])]

        # --- lifecycle: pause, then revoking the source grant blocks re-activation (MAND-06)
        act_as(agent)
        assert client.post(f"/v1/mandates/{mid}:pause").json()["status"] == "PAUSED"
        act_as(cfo)
        assert client.post(f"/v1/authority-grants/{gid}:revoke").json()["status"] == "REVOKED"
        revoked = asyncio.run(audited("authority.grant.revoked"))
        assert len(revoked) == 1 and revoked[0]["body"]["details"]["grantee_principal_id"] == clerk
        assert client.post(f"/v1/authority-grants/{uid()}:revoke").status_code == 404
        r = client.post(f"/v1/mandates/{mid}:activate", params={"risk_class": 2})
        assert r.status_code == 403 and r.json()["error"]["code"] != "MANDATE_SOURCE_INVALID"
        act_as(
            adm
        )  # an administrator holds `mandate.manage`, so the grant check is what stops activation
        r = client.post(f"/v1/mandates/{mid}:activate", params={"risk_class": 2})
        assert r.status_code == 403 and r.json()["error"]["code"] == "MANDATE_SOURCE_INVALID"
        act_as(cfo)
        assert client.post(f"/v1/mandates/{mid}:revoke").json()["status"] == "REVOKED"

    # --- a cell that is not the tenant's writer refuses ledger writes
    other = create_app(Settings(database_url=rt_url, cell_uuid=str(uuid.uuid4())))
    rc = RequestContext("req_o", "cor_o", "api", CELL, tenant_id=tenant, principal_id=agent)
    other.dependency_overrides[authenticated_caller] = lambda: Caller(
        rc, AuthnFact(3, NOW), client_id="c1"
    )
    with TestClient(other) as client:
        r = client.post(f"/v1/mandate-reservations/{res1['reservation_id']}:release")
        assert r.status_code == 409 and r.json()["error"]["code"] == "WRITER_EPOCH_STALE"
        assert r.json()["error"]["retryable"] is True
