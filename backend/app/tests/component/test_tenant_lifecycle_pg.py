"""Tenant lifecycle and OWNER bootstrap end to end (G-W0; ZID-OVERLAY-001 §2-4; OVL-13, OVL-14).

A principal of the signup tenant creates a DRAFT tenant; the server generates the tenant id, its
first principal with a *pending* OWNER role and a single-use BOOTSTRAP enrollment ticket (shown once,
never replayed). Invitations create MEMBER/ADMIN only, an invited MEMBER cannot make itself OWNER,
the last OWNER cannot be removed, owner changes need an independent approver, suspension stops all
protected decisions until a governed resume, and a CLOSED tenant no longer authenticates. The
database refuses an invited OWNER and edits to history on its own.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from admin_fixture import make_admin
from conftest import ctx, engine
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from zid_crypto import KeyRing, LocalSigner
from zid_db import tenant_transaction
from zid_platform.main import create_app
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
from zid_platform.modules.tenant.application import lifecycle
from zid_platform.modules.tenant.domain.lifecycle import Move
from zid_platform.protected import transaction_context
from zid_platform.settings import Settings

ISS, AUD = "https://id.zoiko.test", "https://api.zoiko.test"
CONSOLE_CB = "https://console.zoiko.test/auth/callback"
ADMIN_ACTIONS = ["tenant.member.manage", "tenant.owner.change", "tenant.lifecycle.manage"]


def uid() -> str:
    return str(uuid.uuid4())


def client(cid: str, tenant: str) -> OAuthClient:
    return OAuthClient(
        cid,
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
    )


def test_tenant_bootstrap_roles_lifecycle_and_guards(rt_url: str, owner_url: str) -> None:
    signup, creator, outsider_tenant, outsider = uid(), uid(), uid(), uid()
    signup_client, outsider_client = f"signup-{uid()[:8]}", f"other-{uid()[:8]}"
    ring = KeyRing(LocalSigner("issuer-tenant"))
    sessions: dict[str, tuple[str, str, str]] = {}  # principal -> (tenant, session, client)

    async def session_for(c: Any, tenant: str, p: str, cid: str, level: int = 2) -> None:
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
                level,
                principal.security_revision,
                1,
                SessionStatus.ACTIVE,
                now + timedelta(hours=1),
                now,
            ),
        )
        sessions[p] = (tenant, sid, cid)

    async def seed() -> None:
        async with engine(rt_url) as eng:
            for tenant, p, cid in (
                (signup, creator, signup_client),
                (outsider_tenant, outsider, outsider_client),
            ):
                async with tenant_transaction(eng, ctx(tenant)) as c:
                    await create_client(c, client(cid, tenant))
                    await create_principal(
                        c, Principal(p, tenant, PrincipalType.HUMAN, PrincipalStatus.ACTIVE, "uk-1")
                    )
                    await session_for(c, tenant, p, cid)

    asyncio.run(seed())
    app = create_app(
        Settings(
            database_url=rt_url,
            token_issuer=ISS,
            token_audience=AUD,
            public_base_url=AUD,
            environment="test",
            cell_id="uk-1",
            tenant_bootstrap_tenant_id=signup,
            tenant_console_redirect_uris=(CONSOLE_CB,),
        ),
        keyring=ring,
    )

    def bearer(p: str) -> dict[str, str]:
        tenant, sid, cid = sessions[p]
        now = datetime.now(UTC)
        tok = issue_access_token(
            ring,
            issuer=ISS,
            audience=AUD,
            subject=p,
            tenant_id=tenant,
            client_id=cid,
            now=now,
            session_id=sid,
            auth_time=now - timedelta(seconds=5),
        )
        return {"Authorization": f"Bearer {tok}"}

    def code(r: Any) -> str:
        return str(r.json()["error"]["code"])

    body = {"display_name": "Acme Ltd", "tenant_slug": f"acme-{uid()[:6]}", "deployment_mode": "O2"}
    with TestClient(app) as api:
        # --- bootstrap is only from the signup tenant --------------------------------------------
        r = api.post(
            "/v1/tenants", json=body, headers={**bearer(outsider), "Idempotency-Key": "k0"}
        )
        assert r.status_code == 403 and code(r) == "TENANT_BOOTSTRAP_DISABLED"
        r = api.post(
            "/v1/tenants",
            json={**body, "tenant_id": uid()},  # the client never names the tenant
            headers={**bearer(creator), "Idempotency-Key": "k1"},
        )
        assert r.status_code == 422
        r = api.post("/v1/tenants", json=body, headers={**bearer(creator), "Idempotency-Key": "k1"})
        assert r.status_code == 201, r.text
        created = r.json()
        tenant = created["tenant"]["tenant_id"]
        owner = created["owner_principal_id"]
        assert (
            created["tenant"]["status"] == "DRAFT" and created["tenant"]["home_cell_id"] == "uk-1"
        )
        assert created["owner_role_status"] == "PENDING_VERIFICATION"
        assert created["enrollment_ticket"] and created["console_client_id"]
        replay = api.post(
            "/v1/tenants", json=body, headers={**bearer(creator), "Idempotency-Key": "k1"}
        )
        assert replay.status_code == 201 and replay.json()["tenant"]["tenant_id"] == tenant
        assert replay.json()["enrollment_ticket"] is None  # a credential is never replayed
        r = api.post("/v1/tenants", json=body, headers={**bearer(creator), "Idempotency-Key": "k2"})
        assert r.status_code == 409 and code(r) == "TENANT_SLUG_TAKEN"
        for i in (3, 4):
            r = api.post(
                "/v1/tenants",
                json={**body, "tenant_slug": f"spare-{i}-{uid()[:6]}"},
                headers={**bearer(creator), "Idempotency-Key": f"k{i}"},
            )
            assert r.status_code == 201
        r = api.post(
            "/v1/tenants",
            json={**body, "tenant_slug": f"spare-5-{uid()[:6]}"},
            headers={**bearer(creator), "Idempotency-Key": "k5"},
        )
        assert r.status_code == 409 and code(r) == "TENANT_BOOTSTRAP_LIMIT"

        # --- the pending OWNER reads its tenant but cannot administer it yet ----------------------
        console = created["console_client_id"]
        member: dict[str, str] = {}

        async def owner_session() -> None:
            async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
                await session_for(c, tenant, owner, console)

        asyncio.run(owner_session())
        r = api.get("/v1/tenant", headers=bearer(owner))
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "DRAFT"
        assert [a["status"] for a in r.json()["my_roles"]] == ["PENDING_VERIFICATION"]
        assert (
            api.get("/v1/tenant/transitions", headers=bearer(owner)).json()["items"][0]["to_status"]
            == "DRAFT"
        )
        r = api.post("/v1/tenant/members", json={"role": "MEMBER"}, headers=bearer(owner))
        assert r.status_code == 403  # no authority before the organization gate

        # --- the organization gate (G-W1) activates the OWNER; admin authority is seeded ---------
        second = uid()

        async def gate() -> None:
            async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
                for _ in range(2):  # DRAFT -> ORGANIZATION_VERIFYING -> CONFIGURING
                    await lifecycle.transition(
                        c, tenant, Move.ADVANCE, reason_code="ORG_GATE", actor=owner
                    )
                await lifecycle.activate_bootstrap_owner(c, tenant)
                await create_principal(
                    c,
                    Principal(second, tenant, PrincipalType.HUMAN, PrincipalStatus.ACTIVE, "uk-1"),
                )
                await session_for(c, tenant, second, console)
                await make_admin(c, tenant, [owner, second], ADMIN_ACTIONS, [f"tenant:{tenant}"])

        asyncio.run(gate())
        r = api.get("/v1/tenant", headers=bearer(owner))
        assert [a["status"] for a in r.json()["my_roles"]] == ["ACTIVE"]

        # --- invitations: MEMBER by default, never OWNER (OVL-13) ---------------------------------
        r = api.post("/v1/tenant/members", json={"role": "OWNER"}, headers=bearer(owner))
        assert r.status_code == 403 and code(r) == "TENANT_OWNER_UNAUTHORIZED"
        r = api.post("/v1/tenant/members", json={}, headers=bearer(owner))
        assert r.status_code == 201, r.text
        inv = r.json()
        assert inv["role"] == "MEMBER" and inv["status"] == "INVITED" and inv["enrollment_ticket"]
        member["id"] = inv["principal_id"]

        async def member_session() -> None:
            async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
                await session_for(c, tenant, member["id"], console)

        asyncio.run(member_session())
        r = api.post("/v1/tenant/members/me:accept", headers=bearer(member["id"]))
        assert r.status_code == 200 and r.json()["status"] == "ACTIVE"
        assert r.json()["role"] == "MEMBER"

        # --- an invited MEMBER cannot self-promote (OVL-14) -------------------------------------
        r = api.post(
            "/v1/tenant/owners",
            json={"principal_id": member["id"], "approval_request_id": uid()},
            headers=bearer(member["id"]),
        )
        assert r.status_code == 403
        r = api.post("/v1/tenant/members", json={"role": "ADMIN"}, headers=bearer(member["id"]))
        assert r.status_code == 403  # a MEMBER holds no member-management authority

        # --- owner changes need an independent approver ----------------------------------------
        async def grant_of(p: str) -> str:
            async with engine(owner_url) as eng, eng.begin() as c:
                return str(
                    (
                        await c.execute(
                            text(
                                "SELECT authority_grant_id FROM authz.authority_grant "
                                "WHERE grantee_principal_id = :p"
                            ),
                            {"p": uuid.UUID(p)},
                        )
                    ).scalar_one()
                )

        def approval(action: str, change: str, by: str) -> str:
            tx = transaction_context(action, f"tenant:{tenant}", change)
            r = api.post(
                "/v1/approval-requests",
                headers=bearer(owner),
                json={
                    "subject_ref": f"tenant:{tenant}",
                    "transaction_context": tx,
                    "roles": [action],
                },
            )
            assert r.status_code == 201, r.text
            rid = str(r.json()["approval_request_id"])
            r = api.post(
                f"/v1/approval-requests/{rid}/decisions",
                headers=bearer(by),
                json={
                    "verdict": "APPROVE",
                    "authority_grant_id": asyncio.run(grant_of(by)),
                    "transaction_context": tx,
                },
            )
            assert r.status_code == 201, r.text
            return rid

        r = api.post(
            f"/v1/tenant/owners/{owner}:remove",
            json={
                "approval_request_id": approval(
                    "tenant.owner.change", f"REMOVE_OWNER:{owner}", second
                )
            },
            headers=bearer(owner),
        )
        assert r.status_code == 409 and code(r) == "TENANT_LAST_OWNER"
        rid = approval("tenant.owner.change", f"ADD_OWNER:{second}", second)
        r = api.post(
            "/v1/tenant/owners",
            json={"principal_id": member["id"], "approval_request_id": rid},
            headers=bearer(owner),
        )
        assert r.status_code == 403  # the approval binds the exact change (another principal)
        r = api.post(
            "/v1/tenant/owners",
            json={"principal_id": second, "approval_request_id": rid},
            headers=bearer(owner),
        )
        assert r.status_code == 201 and r.json()["origin"] == "OWNER_CHANGE", r.text
        assert r.json()["approved_by"] == second
        revs = api.get("/v1/tenant/configuration-revisions", headers=bearer(owner)).json()["items"]
        assert revs[0]["change_kind"] == "owner.added"

        # --- suspension stops protected decisions until a governed resume ----------------------
        r = api.post(
            "/v1/tenant:suspend", json={"reason_code": "SECURITY_REVIEW"}, headers=bearer(owner)
        )
        assert r.status_code == 200 and r.json()["status"] == "SUSPENDED", r.text
        assert r.json()["status_before"] == "CONFIGURING"
        r = api.post("/v1/tenant/members", json={}, headers=bearer(owner))
        assert r.status_code == 403 and code(r) == "TENANT_SUSPENDED"
        rid = approval("tenant.lifecycle.manage", "RESUME", second)
        r = api.post("/v1/tenant:resume", json={"approval_request_id": rid}, headers=bearer(owner))
        assert r.status_code == 200 and r.json()["status"] == "CONFIGURING", r.text

    # --- history, the database guards and CLOSED ----------------------------------------------
    async def database_rules() -> None:
        async with engine(rt_url) as eng:
            async with tenant_transaction(eng, ctx(tenant)) as c:
                rows = (
                    (
                        await c.execute(
                            text(
                                "SELECT move FROM platform.tenant_state_transition ORDER BY occurred_at"
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                assert rows == ["CREATE", "ADVANCE", "ADVANCE", "SUSPEND", "RESUME"]
                events = (
                    (
                        await c.execute(
                            text(
                                "SELECT event_type FROM events.outbox_event WHERE tenant_id = :t "
                                "ORDER BY occurred_at"
                            ),
                            {"t": uuid.UUID(tenant)},
                        )
                    )
                    .scalars()
                    .all()
                )
                for name in (
                    "tenant.created",
                    "tenant.owner_changed",
                    "tenant.suspended",
                    "tenant.resumed",
                ):
                    assert f"com.zoiko.id.{name}.v1" in events
            for sql in (
                "UPDATE platform.tenant_state_transition SET reason_code = 'X'",
                (
                    "INSERT INTO platform.tenant_role_assignment (assignment_id, tenant_id, "
                    f"principal_id, role, status, origin) VALUES ('{uid()}', '{tenant}', '{uid()}', "
                    "'OWNER', 'INVITED', 'INVITATION')"
                ),
                f"UPDATE platform.tenant_role_assignment SET role = 'OWNER' WHERE principal_id = '{member['id']}'",
            ):
                with pytest.raises(DBAPIError):
                    async with tenant_transaction(eng, ctx(tenant)) as c:
                        await c.execute(text(sql))
            async with tenant_transaction(eng, ctx(tenant)) as c:
                for move in (Move.OFFBOARD, Move.CLOSE):
                    await lifecycle.transition(c, tenant, move, reason_code="EXIT", actor=owner)
            async with tenant_transaction(eng, ctx(signup)) as c:
                status = (
                    await c.execute(
                        text(
                            "SELECT status FROM platform.tenant_bootstrap_request "
                            "WHERE new_tenant_id = :t"
                        ),
                        {"t": uuid.UUID(tenant)},
                    )
                ).scalar_one()
                assert status == "COMPLETED"  # completed at the gate; closing later keeps that

    asyncio.run(database_rules())
    from zid_platform.modules.tenant.application.routing import forget

    forget(tenant)
    with TestClient(app) as api:
        r = api.get("/v1/tenant", headers=bearer(owner))
        assert r.status_code == 403 and r.json()["error"]["code"] == "TENANT_CLOSED"
