"""RC-4 sovereign / lockbox recovery (ZID-AUTH-001 §14; AUTH-13).

Placing an account under lockbox is an approved R3 change bound to the exact approvers, k and
cooling-off. Its recovery then needs IAL-Z3 re-proofing (Z2 is not enough), approval by k of the
*designated* approvers (another administrator's approval does not count), and a cooling-off of
at least 7 days before the enrollment ticket is issued. The policy cannot be lifted while a case
is open, and lifting it needs its own approval.
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
from zid_platform.modules.authentication.api.recovery import POLICY_ACTION, lockbox_change
from zid_platform.modules.authentication.domain.recovery import LockboxPolicy
from zid_platform.modules.authentication.domain.recovery import (
    transaction_context as recovery_tx,
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
from zid_platform.modules.session.domain.session import Session
from zid_platform.modules.session.domain.session import SessionStatus as SessionStatus_
from zid_platform.modules.session.infrastructure.oauth_store import create_client
from zid_platform.modules.session.infrastructure.session_store import create_session
from zid_platform.modules.session.ports import issue_access_token
from zid_platform.modules.verification.domain.verification import ClaimResult, SessionStatus
from zid_platform.modules.verification.infrastructure import verification_store as vstore
from zid_platform.protected import transaction_context
from zid_platform.settings import Settings

ISS, AUD = "https://id.zoiko.test", "https://api.zoiko.test"
ACTIONS = [
    "authentication.recovery.approve",
    "authentication.recovery.assist",
    POLICY_ACTION,
]


def uid() -> str:
    return str(uuid.uuid4())


def test_lockbox_recovery(rt_url: str, owner_url: str) -> None:
    tenant, vip, admin, a1, a2, other = uid(), uid(), uid(), uid(), uid(), uid()
    web = f"web-{uid()}"
    ring = KeyRing(LocalSigner("issuer-1"))
    people = (vip, admin, a1, a2, other)

    async def seed() -> dict[str, str]:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            for p in people:
                await create_principal(
                    c, Principal(p, tenant, PrincipalType.HUMAN, PrincipalStatus.ACTIVE, "uk-1")
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
            await make_admin(
                c, tenant, [admin, a1, a2, other], ACTIONS, ["principal:*", "recovery_case:*"]
            )
            sessions = {}
            for p in (admin, a1, a2, other):
                sid, now = uid(), datetime.now(UTC)
                principal, _ = await load_principal(c, p)
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
                        SessionStatus_.ACTIVE,
                        now + timedelta(hours=1),
                        now,
                    ),
                )
                sessions[p] = sid
            return sessions

    sessions = asyncio.run(seed())
    app = create_app(
        Settings(
            database_url=rt_url,
            token_issuer=ISS,
            token_audience=AUD,
            public_base_url=AUD,
            environment="test",
        ),
        keyring=ring,
    )

    def bearer(sub: str) -> dict[str, str]:
        now = datetime.now(UTC)
        tok = issue_access_token(
            ring,
            issuer=ISS,
            audience=AUD,
            subject=sub,
            tenant_id=tenant,
            client_id=web,
            now=now,
            session_id=sessions[sub],
            auth_time=now - timedelta(seconds=5),
        )
        return {"Authorization": f"Bearer {tok}"}

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

    async def reproof(level: str) -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            pid, _ = await vstore.create_profile(
                c,
                tenant_id=tenant,
                name=f"recovery-{level}-{uid()[:8]}",
                level=level,
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
                vip,
                "recovery",
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
                (ClaimResult("legal_name", True, "V I P", "doc", ("GOVERNMENT_ID",)),),
                adapter_id="sim",
                reasons=("VERIFIED",),
                evidence_commitment=None,
                now=now,
            )

    with TestClient(app, base_url=ISS) as http:

        def approval(tx: str, roles: list[str], approvers: list[str], k: int = 1) -> str:
            body: dict[str, Any] = {
                "subject_ref": f"principal:{vip}",
                "transaction_context": tx,
                "roles": roles,
            }
            if k > 1:
                body |= {"mode": "K_OF_N", "k": k}
            r = http.post("/v1/approval-requests", headers=bearer(admin), json=body)
            assert r.status_code == 201, r.text
            rid = r.json()["approval_request_id"]
            for p in approvers:
                r = http.post(
                    f"/v1/approval-requests/{rid}/decisions",
                    headers=bearer(p),
                    json={
                        "verdict": "APPROVE",
                        "authority_grant_id": asyncio.run(grant_of(p)),
                        "transaction_context": tx,
                    },
                )
                assert r.status_code == 201, r.text
            return rid

        path = f"/admin/recovery-policies/{vip}"
        body = {"approvers": [a1, a2], "k": 2, "cooling_off_hours": 168}
        policy = LockboxPolicy(vip, tuple(sorted([a1, a2])), 2, timedelta(hours=168))
        resource = f"principal:{vip}"

        # placing an account under lockbox: R3 with a second administrator, exactly this policy
        r = http.put(path, headers=bearer(admin), json=body)
        assert r.status_code == 403 and r.json()["error"]["code"] == "APPROVAL_REQUIRED"
        r = http.put(path, headers=bearer(admin), json={**body, "approvers": [vip, a1]})
        assert r.status_code == 422  # never one's own approver
        r = http.put(path, headers=bearer(admin), json={**body, "cooling_off_hours": 24})
        assert r.status_code == 422  # RC-4 waits at least 7 days
        tx = transaction_context(POLICY_ACTION, resource, lockbox_change(policy))
        ok = approval(tx, [POLICY_ACTION], [other])
        r = http.put(path, headers=bearer(admin), json={**body, "k": 2, "approvers": [a1, other]})
        assert r.status_code == 403  # the approval was for a different set of approvers
        r = http.put(path, headers=bearer(admin), json={**body, "approval_request_id": ok})
        assert r.status_code == 200, r.text
        assert r.json()["recovery_class"] == "RC-4" and r.json()["revision"] == 1
        assert http.get(path, headers=bearer(admin)).json()["k"] == 2

        # recovery of a lockbox account is RC-4
        r = http.post("/admin/recovery-cases", headers=bearer(admin), json={"principal_id": vip})
        assert r.status_code == 201, r.text
        case = r.json()
        assert case["recovery_class"] == "RC-4" and case["status"] == "PENDING_REPROOF"

        def advance(**kw: str) -> dict[str, Any]:
            r = http.post(
                f"/authn/recovery/{case['recovery_id']}:continue",
                json={"client_id": web, "recovery_handle": case["recovery_handle"], **kw},
            )
            assert r.status_code == 200, r.text
            return r.json()

        # the policy cannot be lifted (downgraded) while the case is open
        lift_ok = approval(
            transaction_context(POLICY_ACTION, resource, "lift"), [POLICY_ACTION], [other]
        )
        r = http.post(f"{path}:lift", headers=bearer(admin), json={"approval_request_id": lift_ok})
        assert r.status_code == 409 and r.json()["error"]["code"] == "RECOVERY_IN_PROGRESS"

        # IAL-Z2 re-proofing is not enough for RC-4; Z3 is
        asyncio.run(reproof("IAL-Z2"))
        assert advance()["status"] == "PENDING_REPROOF"
        asyncio.run(reproof("IAL-Z3"))
        out = advance()
        assert out["status"] == "PENDING_APPROVAL"
        assert out["missing"] == ["DESIGNATED_APPROVERS_REQUIRED"]

        # two approvals, but only one from a designated approver: not enough
        rtx = recovery_tx(case["recovery_id"])
        roles = ["authentication.recovery.approve"]
        mixed = approval(rtx, roles, [other, a1], k=2)
        assert advance(approval_request_id=mixed)["status"] == "PENDING_APPROVAL"
        # k designated approvers: cooling-off of the policy's length starts now
        designated = approval(rtx, roles, [a1, a2], k=2)
        cooling = advance(approval_request_id=designated)
        assert cooling["status"] == "COOLING_OFF"
        until = datetime.fromisoformat(cooling["cooling_off_until"])
        assert until - datetime.now(UTC) > timedelta(days=6, hours=23)
        assert advance()["status"] == "COOLING_OFF" and "enrollment_ticket" not in advance()

        async def fast_forward() -> None:
            async with engine(owner_url) as eng, eng.begin() as c:
                await c.execute(
                    text(
                        "UPDATE authn.recovery_case SET cooling_off_until = now() - interval '1 second' "
                        "WHERE recovery_id = :r"
                    ),
                    {"r": uuid.UUID(case["recovery_id"])},
                )

        asyncio.run(fast_forward())
        ready = advance()
        assert ready["status"] == "READY" and ready["enrollment_ticket"]
