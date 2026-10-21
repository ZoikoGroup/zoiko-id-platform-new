"""Operator access end to end over the API (F-W4; DEC-F4; SEC-001 §11-13; EVID-17).

Zero standing privilege: a JIT request is approved by someone else (two people for R3), activated
by the requester, bounded in time, and - for customer data where the tenant requires it - only
usable while the customer's lockbox approval stands. Break-glass is immediate but alerts and leaves a
mandatory independent review. Every step leaves a protected AuditRecord: operator-plane changes in
the platform-security partition, lockbox decisions in the customer's own partition.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from admin_fixture import make_admin
from conftest import ctx, engine
from fastapi.testclient import TestClient
from sqlalchemy import text
from zid_crypto import KeyRing, LocalSigner
from zid_db import tenant_transaction
from zid_platform.main import create_app
from zid_platform.modules.operator_access.application import service
from zid_platform.modules.operator_access.domain.model import OperatorRole
from zid_platform.modules.operator_access.ports import check_capability, expire_due
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
from zid_platform.settings import Settings

ISS, AUD = "https://id.zoiko.test", "https://api.zoiko.test"


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


def test_operator_jit_lockbox_break_glass_and_evidence(rt_url: str, owner_url: str) -> None:
    op_tenant, customer = uid(), uid()
    alice, bob, carol, dave = uid(), uid(), uid(), uid()  # operators
    cust_admin, cust_user = uid(), uid()
    ring = KeyRing(LocalSigner("issuer-op"))
    OPERATOR_CLIENT, WEB, CUST_WEB = (
        f"{n}-{uid()[:8]}" for n in ("zoiko-operator", "web-op", "web-cust")
    )
    web_client = {op_tenant: WEB, customer: CUST_WEB}
    sessions: dict[str, tuple[str, str, int]] = {}  # principal -> (tenant, session, level)

    async def session_for(c: Any, tenant: str, p: str, level: int) -> None:
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
        sessions[p] = (tenant, sid, level)

    async def seed() -> None:
        now = datetime.now(UTC)
        async with engine(rt_url) as eng:
            async with tenant_transaction(eng, ctx(op_tenant)) as c:
                await create_client(c, client(OPERATOR_CLIENT, op_tenant))
                await create_client(c, client(WEB, op_tenant))
                for p in (alice, bob, carol, dave):
                    await create_principal(
                        c,
                        Principal(
                            p, op_tenant, PrincipalType.HUMAN, PrincipalStatus.ACTIVE, "uk-1"
                        ),
                    )
                    await session_for(c, op_tenant, p, 2)
                roles = {
                    alice: {OperatorRole.REQUESTER},
                    bob: {OperatorRole.REQUESTER, OperatorRole.APPROVER},
                    carol: {OperatorRole.APPROVER, OperatorRole.REVIEWER},
                    dave: {OperatorRole.REQUESTER},
                }
                for p, r in roles.items():
                    await service.register_operator(
                        c,
                        tenant_id=op_tenant,
                        principal_id=p,
                        jurisdiction="GB",
                        roles=frozenset(r),
                        now=now,
                    )
            async with tenant_transaction(eng, ctx(customer)) as c:
                await create_client(c, client(CUST_WEB, customer))
                for p in (cust_admin, cust_user):
                    await create_principal(
                        c,
                        Principal(p, customer, PrincipalType.HUMAN, PrincipalStatus.ACTIVE, "uk-1"),
                    )
                await session_for(c, customer, cust_admin, 2)
                await session_for(c, customer, cust_user, 2)
                await make_admin(
                    c,
                    customer,
                    cust_admin,
                    ["operator.lockbox.approve", "operator.access_policy.manage"],
                    ["lockbox_request:*", "operator_access_policy"],
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
            operator_client_ids=(OPERATOR_CLIENT,),
        ),
        keyring=ring,
    )

    def bearer(p: str, *, cid: str | None = None) -> dict[str, str]:
        tenant, sid, _ = sessions[p]
        cid = cid or OPERATOR_CLIENT
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

    def web(p: str) -> dict[str, str]:
        return bearer(p, cid=web_client[sessions[p][0]])

    access = {
        "capability": "tenant.data.read",
        "case_ref": "INC-1001",
        "reason": "customer reported missing records",
        "target_tenant_id": customer,
        "resource_scope": "principal:*",
        "duration_minutes": 30,
    }

    async def capability(op: str, cap: str, resource: str) -> Any:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(op_tenant)) as c:
            return await check_capability(
                c,
                operator_principal_id=op,
                capability_name=cap,
                tenant_id=customer,
                resource=resource,
                now=datetime.now(UTC),
            )

    with TestClient(app) as api:
        # --- the operator gate ---------------------------------------------------------------
        r = api.get("/operator/capabilities", headers=web(alice))
        assert r.status_code == 403 and r.json()["error"]["code"] == "OPERATOR_CLIENT_REQUIRED"
        r = api.get("/operator/capabilities", headers=web(cust_user))
        assert r.status_code == 403
        assert api.get("/operator/capabilities", headers=bearer(alice)).status_code == 200
        r = api.post(
            "/operator/access-requests", json={**access, "cell_id": "eu-1"}, headers=bearer(alice)
        )
        assert r.status_code == 422  # cell/environment are never caller input (INV-13)

        # --- the customer requires its lockbox for operator data access -------------------------
        r = api.put(
            "/v1/operator-access-policy",
            json={"lockbox_required": True, "allowed_operator_jurisdictions": ["GB"]},
            headers=web(cust_admin),
        )
        assert r.status_code == 200, r.text

        r = api.post("/operator/access-requests", json=access, headers=bearer(alice))
        assert r.status_code == 201, r.text
        g = r.json()
        assert g["lockbox_required"] and g["state"] == "REQUESTED" and g["cell_id"] == "uk-1"
        gid = g["jit_grant_id"]
        r = api.post(f"/operator/access-requests/{gid}:approve", headers=bearer(alice))
        assert r.status_code == 403  # nobody approves their own request
        r = api.post(f"/operator/access-requests/{gid}:approve", headers=bearer(bob))
        assert r.status_code == 200 and r.json()["state"] == "APPROVED", r.text
        r = api.post(f"/operator/access-requests/{gid}:activate", headers=bearer(alice))
        assert r.status_code == 403 and r.json()["error"]["code"] == "LOCKBOX_APPROVAL_REQUIRED"
        assert not asyncio.run(capability(alice, "tenant.data.read", "principal:1")).allowed

        lockboxes = api.get("/v1/lockbox-requests", headers=web(cust_admin)).json()["items"]
        assert len(lockboxes) == 1 and lockboxes[0]["jit_grant_id"] == gid
        lb = lockboxes[0]["lockbox_request_id"]
        r = api.post(
            f"/v1/lockbox-requests/{lb}:approve", json={"reason": "ok"}, headers=web(cust_admin)
        )
        assert r.status_code == 200 and r.json()["state"] == "APPROVED", r.text
        r = api.post(f"/operator/access-requests/{gid}:activate", headers=bearer(alice))
        assert r.status_code == 200 and r.json()["state"] == "ACTIVE", r.text
        assert asyncio.run(capability(alice, "tenant.data.read", "principal:1")).allowed
        assert not asyncio.run(capability(alice, "tenant.data.read", "mandate:1")).allowed  # scope
        assert not asyncio.run(
            capability(bob, "tenant.data.read", "principal:1")
        ).allowed  # not his

        # the customer withdraws: access stops at once (EVID-17)
        r = api.post(
            f"/v1/lockbox-requests/{lb}:deny",
            json={"reason": "case closed"},
            headers=web(cust_admin),
        )
        assert r.status_code == 200 and r.json()["state"] == "REVOKED"
        check = asyncio.run(capability(alice, "tenant.data.read", "principal:1"))
        assert not check.allowed and check.reason == "LOCKBOX_APPROVAL_WITHDRAWN"

        # --- two-person control for R3 ----------------------------------------------------------
        cfg = {**access, "capability": "tenant.config.change", "resource_scope": "config:*"}
        g2 = api.post("/operator/access-requests", json=cfg, headers=bearer(alice)).json()
        assert g2["required_approvals"] == 2
        r = api.post(f"/operator/access-requests/{g2['jit_grant_id']}:approve", headers=bearer(bob))
        assert r.json()["state"] == "REQUESTED" and r.json()["approvals_received"] == 1
        r = api.post(f"/operator/access-requests/{g2['jit_grant_id']}:approve", headers=bearer(bob))
        assert r.status_code == 403  # the same approver never counts twice
        r = api.post(
            f"/operator/access-requests/{g2['jit_grant_id']}:approve", headers=bearer(carol)
        )
        assert r.json()["state"] == "APPROVED"

        # --- break-glass: immediate, alerted, independently reviewed -----------------------------
        bg = {
            **access,
            "capability": "security.policy",
            "target_tenant_id": None,
            "resource_scope": "policy:*",
            "reason": "active credential-stuffing attack on login",
        }
        r = api.post("/operator/break-glass", json=bg, headers=bearer(dave))
        assert r.status_code == 201, r.text
        b = r.json()
        assert b["state"] == "ACTIVE" and b["break_glass"] and b["evidence_class"] == "E3"
        assert b["duration_minutes"] <= 60
        r = api.post(
            f"/operator/reviews/{b['review_id']}:complete",
            json={"outcome": "JUSTIFIED", "notes": "attack confirmed"},
            headers=bearer(dave),
        )
        assert r.status_code == 403  # the requester never reviews their own break-glass
        r = api.post(
            f"/operator/reviews/{b['review_id']}:complete",
            json={"outcome": "JUSTIFIED", "notes": "attack confirmed"},
            headers=bearer(carol),
        )
        assert r.status_code == 200 and r.json()["state"] != "REVIEW_PENDING", r.text
        # separation of duties: holding security.policy, dave cannot also take key.admin
        r = api.post(
            "/operator/access-requests",
            json={
                **bg,
                "capability": "key.admin",
                "resource_scope": "key:*",
                "reason": "rotate the evidence key now",
            },
            headers=bearer(dave),
        )
        assert r.status_code == 403 and r.json()["error"]["code"] == "OPERATOR_SOD_CONFLICT", r.text

    # --- auto-expiry -----------------------------------------------------------------------------
    async def expire() -> list[str]:
        async with engine(owner_url) as eng, eng.begin() as c:
            return await expire_due(c, datetime.now(UTC) + timedelta(hours=2))

    expired = asyncio.run(expire())
    assert b["jit_grant_id"] in expired

    # --- evidence: platform partition for the operator plane, customer partition for lockbox ------
    async def evidence() -> tuple[set[str], set[str]]:
        async with engine(owner_url) as eng, eng.begin() as c:
            platform = set(
                (
                    await c.execute(
                        text("SELECT event_type FROM evidence.audit_record WHERE tenant_id IS NULL")
                    )
                ).scalars()
            )
            mine = set(
                (
                    await c.execute(
                        text("SELECT event_type FROM evidence.audit_record WHERE tenant_id = :t"),
                        {"t": uuid.UUID(customer)},
                    )
                ).scalars()
            )
            return platform, mine

    platform, mine = asyncio.run(evidence())
    assert {
        "operator_access.requested",
        "operator_access.approved",
        "operator_access.activated",
        "operator_access.break_glass.activated",
        "operator_access.review.completed",
        "operator_access.expired",
    } <= platform
    assert {
        "operator_access.lockbox.requested",
        "operator_access.lockbox.approved",
        "operator_access.lockbox.revoked",
        "operator_access.policy.changed",
    } <= mine
