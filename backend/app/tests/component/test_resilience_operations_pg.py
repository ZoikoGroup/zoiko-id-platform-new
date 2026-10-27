"""Governed recovery operations over the API (G-W7; RESILIENCE-001 §11-20, §25-30; RES-10/29/30).

A failover runs its runbook strictly in order: nothing is skipped, CONFIRM steps carry evidence,
gates read the recovery cell's readiness and the tenant's residency, and a failed gate leaves the
operation BLOCKED (a controlled DENY, never split-brain). Cutover advances the placement epoch and
the C3 writer epoch together; after it there is no abort, only a failback. A quarantined cell takes
no new organizations. A DR exercise that misses its target raises `resilience.slo.breached`.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from conftest import ctx, engine
from fastapi.testclient import TestClient
from onboarding_fixture import onboard
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from zid_crypto import KeyRing, LocalSigner
from zid_db import tenant_transaction
from zid_platform.main import create_app
from zid_platform.modules.mandate.ports import advance_writer
from zid_platform.modules.operator_access.application import service as operator_service
from zid_platform.modules.operator_access.domain.model import OperatorRole
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
from zid_platform.modules.session.infrastructure.session_store import create_session
from zid_platform.modules.session.ports import issue_access_token
from zid_platform.modules.tenant.application import lifecycle, residency
from zid_platform.modules.tenant.ports import TenantError
from zid_platform.settings import Settings

ISS, AUD = "https://id.zoiko.test", "https://api.zoiko.test"
PROBES_ALL = {
    p: True
    for p in (
        "authentication",
        "authorization_allow",
        "authorization_deny",
        "mandate",
        "evidence",
        "revocation",
    )
}


def uid() -> str:
    return str(uuid.uuid4())


def code(r: Any) -> str:
    return str(r.json()["error"]["code"])


def test_failover_runbook_gates_epochs_quarantine_and_exercises(
    rt_url: str, owner_url: str
) -> None:
    o = onboard(rt_url)
    customer = o.tenant
    op_tenant = uid()
    alice, bob, carol, dave = uid(), uid(), uid(), uid()
    ring = KeyRing(LocalSigner("issuer-res"))
    OPERATOR = f"zoiko-operator-{uid()[:8]}"
    sessions: dict[str, str] = {}

    async def seed() -> None:
        now = datetime.now(UTC)
        async with engine(rt_url) as eng:
            async with tenant_transaction(eng, ctx(op_tenant)) as c:
                await create_client(
                    c,
                    OAuthClient(
                        OPERATOR,
                        op_tenant,
                        "PUBLIC",
                        "STANDARD",
                        ("https://op.example/cb",),
                        frozenset({"authorization_code"}),
                        frozenset({"openid"}),
                        (AUD,),
                        "none",
                        False,
                        "ACTIVE",
                        first_party=True,
                    ),
                )
                for p in (alice, bob, carol, dave):
                    await create_principal(
                        c,
                        Principal(
                            p, op_tenant, PrincipalType.HUMAN, PrincipalStatus.ACTIVE, "uk-1"
                        ),
                    )
                    principal, _ = await load_principal(c, p)
                    sid = uid()
                    await create_session(
                        c,
                        Session(
                            sid,
                            op_tenant,
                            p,
                            now - timedelta(seconds=5),
                            "native:webauthn",
                            2,
                            principal.security_revision,
                            1,
                            SessionStatus.ACTIVE,
                            now + timedelta(hours=1),
                            now,
                        ),
                    )
                    sessions[p] = sid
                roles = {
                    alice: {OperatorRole.REQUESTER},
                    bob: {OperatorRole.APPROVER},
                    carol: {OperatorRole.APPROVER},
                    dave: {OperatorRole.REQUESTER},
                }
                for p, r in roles.items():
                    await operator_service.register_operator(
                        c,
                        tenant_id=op_tenant,
                        principal_id=p,
                        jurisdiction="GB",
                        roles=frozenset(r),
                        now=now,
                    )
            async with engine(owner_url) as oeng, oeng.begin() as oc:
                # the company was migrated to eu-1: its own record moves with it (a profile edit alone cannot)
                await oc.execute(
                    text("UPDATE platform.tenant SET home_cell_id = 'eu-1' WHERE tenant_id = :t"),
                    {"t": uuid.UUID(customer)},
                )
            async with tenant_transaction(eng, ctx(customer)) as c:
                await residency.set_residency(
                    c,
                    customer,
                    profile="SRP-0",
                    home_cell_id="eu-1",
                    recovery_cells=["eu-1-dr"],
                    prohibited_cells=["uk-1"],
                    operator_juris=[],
                    key_profile="PLATFORM_MANAGED",
                    jurisdiction_pack_id="eu-gdpr",
                    approval_ref="TEST-RES",
                    actor=o.owner,
                )
                assert (
                    await advance_writer(
                        c, customer, uuid.uuid5(uuid.NAMESPACE_URL, "zoiko-cell:eu-1")
                    )
                    == 1
                )
        async with engine(owner_url) as eng, eng.begin() as c:  # the tenant is live
            await c.execute(
                text("UPDATE platform.tenant SET status = 'ACTIVE' WHERE tenant_id = :t"),
                {"t": uuid.UUID(customer)},
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
            operator_tenant_id=op_tenant,
            operator_client_ids=(OPERATOR,),
        ),
        keyring=ring,
    )

    def as_(p: str) -> dict[str, str]:
        now = datetime.now(UTC)
        tok = issue_access_token(
            ring,
            issuer=ISS,
            audience=AUD,
            subject=p,
            tenant_id=op_tenant,
            client_id=OPERATOR,
            now=now,
            session_id=sessions[p],
            auth_time=now - timedelta(seconds=5),
        )
        return {"Authorization": f"Bearer {tok}"}

    async def fence() -> tuple[uuid.UUID, int]:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(customer)) as c:
            row = (await c.execute(text("SELECT cell_id, epoch FROM agent.writer_fence"))).one()
            return row.cell_id, int(row.epoch)

    async def placement() -> tuple[str, int, str]:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(customer)) as c:
            r = (
                await c.execute(
                    text("SELECT home_cell_id, placement_epoch FROM platform.tenant_residency")
                )
            ).one()
            t = (await c.execute(text("SELECT home_cell_id FROM platform.tenant"))).one()
            return r.home_cell_id, int(r.placement_epoch), t.home_cell_id

    async def events(names: tuple[str, ...]) -> set[str]:
        async with engine(owner_url) as eng, eng.begin() as c:
            rows = (
                await c.execute(
                    text("SELECT event_type FROM events.outbox_event WHERE event_type = ANY(:n)"),
                    {"n": [f"com.zoiko.id.{n}.v1" for n in names]},
                )
            ).all()
            return {r.event_type[len("com.zoiko.id.") : -len(".v1")] for r in rows}

    with TestClient(app) as api:

        def grant(
            capability: str, tenant: str | None, scope: str, approvers: tuple[str, ...]
        ) -> None:
            body: dict[str, Any] = {
                "capability": capability,
                "case_ref": "INC-DR-1",
                "reason": "declared regional disaster drill",
                "resource_scope": scope,
                "duration_minutes": 60,
            }
            if tenant:
                body["target_tenant_id"] = tenant
            r = api.post("/operator/access-requests", json=body, headers=as_(alice))
            assert r.status_code == 201, r.text
            gid = r.json()["jit_grant_id"]
            for a in approvers:
                r = api.post(f"/operator/access-requests/{gid}:approve", headers=as_(a))
                assert r.status_code == 200, r.text
            r = api.post(f"/operator/access-requests/{gid}:activate", headers=as_(alice))
            assert r.status_code == 200 and r.json()["state"] == "ACTIVE", r.text

        # --- no grant, no operation ----------------------------------------------------------
        r = api.post(
            f"/platform/tenants/{customer}:failover",
            json={"target_cell_id": "eu-1-dr"},
            headers=as_(dave),
        )
        assert r.status_code == 403 and code(r) == "OPERATOR_CAPABILITY_REQUIRED"

        grant(
            "platform.dr.execute", customer, f"tenant:{customer}", (bob, carol)
        )  # R3: two approvers
        grant("platform.cell.manage", None, "cell:*", (bob,))

        r = api.put(
            "/platform/cells/eu-1-dr/readiness",
            headers=as_(alice),
            json={"replica_lag_s": 120, "recovery_keys_ready": True, "one_az_loss_headroom": 0.5},
        )
        assert r.status_code == 200, r.text

        # --- residency: a prohibited target is blocked at ASSESS, and the block is on record --
        r = api.post(
            f"/platform/tenants/{customer}:failover",
            json={"target_cell_id": "uk-1"},
            headers=as_(alice),
        )
        assert r.status_code == 201, r.text
        bad = r.json()["operation_id"]
        assert r.json()["source_cell_id"] == "eu-1" and r.json()["epoch_before"] == 1
        r = api.post(
            f"/platform/tenants/{customer}:failover",
            json={"target_cell_id": "eu-1-dr"},
            headers=as_(alice),
        )
        assert r.status_code == 409 and code(r) == "RECOVERY_OPERATION_IN_PROGRESS"
        r = api.post(
            f"/platform/operations/{bad}:advance",
            headers=as_(alice),
            json={"step": "FREEZE_OLD_WRITER", "evidence_ref": "CHG-1"},
        )
        assert r.status_code == 200, r.text
        r = api.post(
            f"/platform/operations/{bad}:advance", headers=as_(alice), json={"step": "ASSESS"}
        )
        assert r.status_code == 409 and code(r) == "RESIDENCY_FAILOVER_BLOCKED", r.text
        r = api.get(f"/platform/operations/{bad}", headers=as_(alice))
        assert r.json()["status"] == "BLOCKED" and r.json()["steps"][-1]["outcome"] == "BLOCKED"
        r = api.post(
            f"/platform/operations/{bad}:abort", json={"reason": "wrong target"}, headers=as_(alice)
        )
        assert r.status_code == 200 and r.json()["status"] == "ABORTED"

        # --- the real failover, strictly in order -------------------------------------------
        r = api.post(
            f"/platform/tenants/{customer}:failover",
            json={"target_cell_id": "eu-1-dr"},
            headers=as_(alice),
        )
        assert r.status_code == 201, r.text
        op = r.json()["operation_id"]
        assert r.json()["next_step"]["name"] == "FREEZE_OLD_WRITER"

        def step(name: str, **kw: Any) -> Any:
            return api.post(
                f"/platform/operations/{op}:advance", json={"step": name, **kw}, headers=as_(alice)
            )

        r = step("ASSESS")
        assert (
            r.status_code == 409 and code(r) == "RECOVERY_STEP_OUT_OF_ORDER"
        )  # no promote before fence
        r = step("FREEZE_OLD_WRITER")
        assert r.status_code == 422 and code(r) == "RECOVERY_EVIDENCE_REQUIRED"
        assert step("FREEZE_OLD_WRITER", evidence_ref="CHG-2").status_code == 200
        r = step("ASSESS")
        assert (
            r.status_code == 409 and code(r) == "RECOVERY_REPLICA_TOO_STALE"
        )  # 120 s > the 30 s RPO
        api.put(
            "/platform/cells/eu-1-dr/readiness",
            headers=as_(alice),
            json={"replica_lag_s": 2.5, "recovery_keys_ready": True, "one_az_loss_headroom": 0.5},
        )
        r = step("ASSESS")
        assert r.status_code == 200 and r.json()["status"] == "OPEN", r.text
        assert {g["gate_id"] for g in r.json()["report"]} >= {
            "residency.target_approved",
            "replica.lag_within_rpo",
        }
        assert step("PROMOTE_DATA", evidence_ref="CHG-3").status_code == 200
        assert step("ACTIVATE_KEYS", evidence_ref="KMS-4").status_code == 200

        # a live tenant's home cell never moves outside a governed operation
        async def direct_move() -> None:
            async with engine(rt_url) as eng, tenant_transaction(eng, ctx(customer)) as c:
                await c.execute(text("UPDATE platform.tenant SET home_cell_id = 'eu-1-dr'"))

        with pytest.raises(DBAPIError):
            asyncio.run(direct_move())

        r = step("ADVANCE_EPOCHS")
        assert r.status_code == 200 and r.json()["epoch_after"] == 2, r.text
        assert asyncio.run(placement()) == ("eu-1-dr", 2, "eu-1-dr")
        assert asyncio.run(fence()) == (
            uuid.uuid5(uuid.NAMESPACE_URL, "zoiko-cell:eu-1-dr"),
            2,
        )  # old writer fenced
        r = api.post(
            f"/platform/operations/{op}:abort",
            json={"reason": "second thoughts"},
            headers=as_(alice),
        )
        assert r.status_code == 409 and code(r) == "RECOVERY_ABORT_AFTER_CUTOVER"

        r = step(
            "VALIDATE", evidence_ref="SYN-5", probes={**PROBES_ALL, "authorization_deny": False}
        )
        assert r.status_code == 409 and code(r) == "RECOVERY_VALIDATION"  # no partial credit
        assert step("VALIDATE", evidence_ref="SYN-5", probes=PROBES_ALL).status_code == 200
        assert step("ROUTE").status_code == 200
        r = step("RECONCILE")
        assert r.status_code == 200 and r.json()["status"] == "COMPLETED", r.text
        assert r.json()["observed_rto_s"] is not None and r.json()["next_step"] is None
        assert step("RECONCILE").status_code == 409  # closed
        log = api.get(f"/platform/operations/{op}", headers=as_(alice)).json()["steps"]
        assert [s["step"] for s in log if s["outcome"] == "DONE"] == [
            "FREEZE_OLD_WRITER",
            "ASSESS",
            "PROMOTE_DATA",
            "ACTIVATE_KEYS",
            "ADVANCE_EPOCHS",
            "VALIDATE",
            "ROUTE",
            "RECONCILE",
        ]

        # --- OPS-25: repeating the runbook is fenced and leaves one audit trail --------------
        for name in ("ADVANCE_EPOCHS", "FREEZE_OLD_WRITER", "ROUTE"):
            r = step(name, evidence_ref="CHG-REPEAT")
            assert r.status_code == 409 and code(r) == "RECOVERY_OPERATION_CLOSED", name
        assert asyncio.run(placement()) == ("eu-1-dr", 2, "eu-1-dr")  # no second epoch advance
        assert asyncio.run(fence())[1] == 2

        async def audited() -> dict[str, int]:
            async with engine(rt_url) as eng, tenant_transaction(eng, ctx(customer)) as c:
                rows = (
                    await c.execute(
                        text(
                            "SELECT event_type, count(*) AS n FROM evidence.audit_record "
                            "WHERE event_type LIKE 'tenant.%' OR event_type = 'writer.epoch.changed' "
                            "GROUP BY event_type"
                        )
                    )
                ).all()
                return {r.event_type: int(r.n) for r in rows}

        trail = asyncio.run(audited())
        assert trail["tenant.placement.changed"] == 1 and trail["writer.epoch.changed"] == 1
        assert trail["tenant.failover.completed"] == 1

        # --- a quarantined cell takes no new organization -----------------------------------
        r = api.post(
            "/platform/cells/eu-1:quarantine", json={"reason": "storage fault"}, headers=as_(alice)
        )
        assert r.status_code == 200 and r.json()["status"] == "QUARANTINED", r.text

        async def provision_on_quarantined() -> None:
            signup, creator = uid(), uid()
            async with engine(rt_url) as eng, tenant_transaction(eng, ctx(signup)) as c:
                await create_principal(
                    c,
                    Principal(creator, signup, PrincipalType.HUMAN, PrincipalStatus.ACTIVE, "eu-1"),
                )
                await lifecycle.bootstrap_tenant(
                    c,
                    creating_tenant_id=signup,
                    creator_principal_id=creator,
                    display_name="Late",
                    tenant_slug=f"late-{uid()[:8]}",
                    deployment_mode="O1",
                    home_cell_id="eu-1",
                    console=None,
                )

        with pytest.raises(TenantError) as e:
            asyncio.run(provision_on_quarantined())
        assert e.value.code == "CELL_UNAVAILABLE"
        r = api.post(
            "/platform/cells/eu-1:restore", json={"reason": "storage repaired"}, headers=as_(alice)
        )
        assert r.json()["previous_status"] == "QUARANTINED"

        # --- exercises: pass/fail is computed, a missed target is an SLO breach --------------
        start = datetime.now(UTC) - timedelta(minutes=10)
        r = api.post(
            "/platform/dr-exercises",
            headers=as_(alice),
            json={
                "kind": "FAILOVER",
                "exercised_cell_id": "eu-1-dr",
                "scenario": "regional loss game day",
                "operation_id": op,
                "observed_rto_s": 420,
                "observed_rpo_s": 3,
                "started_at": start.isoformat(),
                "completed_at": datetime.now(UTC).isoformat(),
            },
        )
        assert (
            r.status_code == 201 and r.json()["passed"] is False and r.json()["target_rto_s"] == 300
        ), r.text
        assert any(
            x["exercise_id"] == r.json()["exercise_id"]
            for x in api.get("/platform/dr-exercises", headers=as_(alice)).json()["exercises"]
        )

    assert asyncio.run(
        events(
            (
                "tenant.failover.started",
                "tenant.failover.failed",
                "tenant.failover.completed",
                "tenant.placement.changed",
                "writer.epoch.changed",
                "cell.quarantined",
                "cell.status.changed",
                "dr.exercise.completed",
                "resilience.slo.breached",
            )
        )
    ) == {
        "tenant.failover.started",
        "tenant.failover.failed",
        "tenant.failover.completed",
        "tenant.placement.changed",
        "writer.epoch.changed",
        "cell.quarantined",
        "cell.status.changed",
        "dr.exercise.completed",
        "resilience.slo.breached",
    }
