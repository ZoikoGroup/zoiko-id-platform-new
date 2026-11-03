"""A governed region migration end to end (ZID-EDR-002 section 6, third test: EDR02-03).

A migration needs a two-person grant (one approver opens nothing), never goes to a cell the tenant's residency prohibits (blocked and
recorded, nothing moves), runs its runbook strictly in order with one writer throughout, moves the placement and the writer epoch together,
and finishes only when the old copy is retired with its erasure evidence.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from conftest import ctx, engine
from fastapi.testclient import TestClient
from onboarding_fixture import onboard
from sqlalchemy import text
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
from zid_platform.modules.tenant.application import residency
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


def test_a_governed_migration_needs_two_approvers_stays_in_policy_and_ends_with_the_old_copy_erased(
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
            async with tenant_transaction(eng, ctx(customer)) as c:
                await residency.set_residency(
                    c,
                    customer,
                    profile="SRP-0",
                    home_cell_id="uk-1",
                    recovery_cells=[],
                    prohibited_cells=["us-1"],
                    operator_juris=[],
                    key_profile="PLATFORM_MANAGED",
                    jurisdiction_pack_id="uk-gdpr",
                    approval_ref="TEST-RES",
                    actor=o.owner,
                )
                assert (
                    await advance_writer(
                        c, customer, uuid.uuid5(uuid.NAMESPACE_URL, "zoiko-cell:uk-1")
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
            capability: str, scope: str, approvers: tuple[str, ...], *, tenant: str | None = None
        ) -> Any:
            body: dict[str, Any] = {
                "capability": capability,
                "case_ref": "CHG-MIG-1",
                "reason": "approved regional migration",
                "resource_scope": scope,
                "duration_minutes": 60,
            }
            if tenant:
                body["target_tenant_id"] = tenant
            r = api.post("/operator/access-requests", json=body, headers=as_(alice))
            assert r.status_code == 201, r.text
            gid = r.json()["jit_grant_id"]
            for a in approvers:
                assert (
                    api.post(f"/operator/access-requests/{gid}:approve", headers=as_(a)).status_code
                    == 200
                )
            return api.post(f"/operator/access-requests/{gid}:activate", headers=as_(alice))

        def move(target: str, who: str = alice) -> Any:
            return api.post(
                f"/platform/tenants/{customer}:migrate",
                json={"target_cell_id": target},
                headers=as_(who),
            )

        # --- no grant, no migration ---------------------------------------------------------
        r = move("eu-1", dave)
        assert r.status_code == 403 and code(r) == "OPERATOR_CAPABILITY_REQUIRED"

        # --- dual authorization: one approver is not enough, two are -------------------------
        one = grant("platform.migration.execute", f"tenant:{customer}", (bob,), tenant=customer)
        assert one.status_code != 200 or one.json()["state"] != "ACTIVE", (
            "one approver must not open a migration"
        )
        assert move("eu-1").status_code == 403, (
            "a migration without an active two-person grant is refused"
        )
        two = grant(
            "platform.migration.execute", f"tenant:{customer}", (bob, carol), tenant=customer
        )
        assert two.status_code == 200 and two.json()["state"] == "ACTIVE", two.text
        assert grant("platform.cell.manage", "cell:*", (bob,)).status_code == 200
        for cell in ("eu-1", "us-1"):
            r = api.put(
                f"/platform/cells/{cell}/readiness",
                headers=as_(alice),
                json={"replica_lag_s": 0, "recovery_keys_ready": True, "one_az_loss_headroom": 0.5},
            )
            assert r.status_code == 200, r.text

        # --- no forbidden destination: a cell the tenant's residency prohibits is blocked at PREPARE ----
        r = move("us-1")
        assert r.status_code == 201, r.text
        bad = r.json()["operation_id"]
        r = api.post(
            f"/platform/operations/{bad}:advance", headers=as_(alice), json={"step": "PREPARE"}
        )
        assert r.status_code == 409, r.text
        assert (
            api.get(f"/platform/operations/{bad}", headers=as_(alice)).json()["status"] == "BLOCKED"
        )
        assert asyncio.run(placement()) == ("uk-1", 1, "uk-1"), "a blocked migration moves nothing"
        assert (
            api.post(
                f"/platform/operations/{bad}:abort",
                json={"reason": "outside residency"},
                headers=as_(alice),
            ).status_code
            == 200
        )

        # --- the approved migration, strictly in order, one writer throughout ----------------
        r = move("eu-1")
        assert r.status_code == 201, r.text
        op = r.json()["operation_id"]
        assert r.json()["source_cell_id"] == "uk-1" and r.json()["next_step"]["name"] == "PREPARE"
        assert move("eu-1").status_code == 409, "a second migration cannot start while one is open"

        def step(name: str, **kw: Any) -> Any:
            return api.post(
                f"/platform/operations/{op}:advance", json={"step": name, **kw}, headers=as_(alice)
            )

        assert code(step("QUIESCE", evidence_ref="CHG-9")) == "RECOVERY_STEP_OUT_OF_ORDER"
        assert step("PREPARE").status_code == 200
        assert code(step("COPY_VERIFIED")) == "RECOVERY_EVIDENCE_REQUIRED"
        assert step("COPY_VERIFIED", evidence_ref="COPY-1").status_code == 200
        assert asyncio.run(fence())[0] == uuid.uuid5(uuid.NAMESPACE_URL, "zoiko-cell:uk-1"), (
            "still one writer, the old cell"
        )
        assert step("QUIESCE", evidence_ref="QUIESCE-1").status_code == 200
        r = step("ADVANCE_EPOCHS")
        assert r.status_code == 200 and r.json()["epoch_after"] == 2, r.text
        assert asyncio.run(placement()) == ("eu-1", 2, "eu-1")
        assert asyncio.run(fence()) == (uuid.uuid5(uuid.NAMESPACE_URL, "zoiko-cell:eu-1"), 2), (
            "the writer moved with the placement"
        )
        assert (
            api.post(
                f"/platform/operations/{op}:abort", json={"reason": "late"}, headers=as_(alice)
            ).status_code
            == 409
        )
        r = step("VALIDATE", evidence_ref="SYN-1", probes={**PROBES_ALL, "evidence": False})
        assert r.status_code == 409 and code(r) == "RECOVERY_VALIDATION"
        assert step("VALIDATE", evidence_ref="SYN-1", probes=PROBES_ALL).status_code == 200
        assert step("ROUTE").status_code == 200
        assert asyncio.run(events(("tenant.migration.completed",))) == set(), (
            "not complete until the old copy is retired"
        )

        # --- post-migration cleanup: the last step needs its erasure evidence ----------------
        assert code(step("RETIRE_OLD_COPY")) == "RECOVERY_EVIDENCE_REQUIRED"
        r = step("RETIRE_OLD_COPY", evidence_ref="ERASE-1")
        assert r.status_code == 200 and r.json()["status"] == "COMPLETED", r.text
        log = api.get(f"/platform/operations/{op}", headers=as_(alice)).json()["steps"]
        assert [s["step"] for s in log if s["outcome"] == "DONE"] == [
            "PREPARE",
            "COPY_VERIFIED",
            "QUIESCE",
            "ADVANCE_EPOCHS",
            "VALIDATE",
            "ROUTE",
            "RETIRE_OLD_COPY",
        ]
        assert next(s for s in log if s["step"] == "RETIRE_OLD_COPY")["evidence_ref"] == "ERASE-1"
        assert asyncio.run(
            events(
                (
                    "tenant.migration.started",
                    "tenant.migration.quiesced",
                    "tenant.migration.cutover",
                    "tenant.migration.completed",
                )
            )
        ) == {
            "tenant.migration.started",
            "tenant.migration.quiesced",
            "tenant.migration.cutover",
            "tenant.migration.completed",
        }
        assert step("ADVANCE_EPOCHS").status_code == 409, (
            "repeating the runbook after completion is refused"
        )
        assert asyncio.run(placement()) == ("eu-1", 2, "eu-1") and asyncio.run(fence())[1] == 2
