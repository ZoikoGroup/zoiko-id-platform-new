"""Account recovery end to end (ZID-AUTH-001 §4, §14, §24; AUTH-13; AUTH-W7).

RC-1: a recovery code issues a 15-minute enrollment ticket; the new passkey completes the
recovery and revokes every older session. RC-3: an administrator's code is not enough - re-proofing,
a different administrator's approval bound to this exact case, then cooling-off; the old factors are
marked LOST. The owner can cancel from a live session; support can open but never complete a case.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from admin_fixture import make_admin
from conftest import ctx, engine
from fastapi.testclient import TestClient
from softauthn import SoftAuthenticator
from sqlalchemy import text
from zid_crypto import KeyRing, LocalSigner
from zid_db import tenant_transaction
from zid_platform.main import create_app
from zid_platform.modules.authentication.domain.recovery import transaction_context
from zid_platform.modules.authentication.infrastructure.authenticator_store import issue_ticket
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
from zid_platform.settings import Settings

ISS, AUD, RP, ORIGIN = (
    "https://id.zoiko.test",
    "https://api.zoiko.test",
    "id.zoiko.test",
    "https://id.zoiko.test",
)
ADMIN_ACTIONS = [
    "authentication.recovery.approve",
    "authentication.recovery.assist",
    "authentication.recovery.deny",
    "session.bulk_revoke",
]


def uid() -> str:
    return str(uuid.uuid4())


def test_recovery_classes_gates_and_completion(rt_url: str, owner_url: str) -> None:
    tenant, alice, boss, approver, support = uid(), uid(), uid(), uid(), uid()
    web = f"web-{uid()}"
    ring = KeyRing(LocalSigner("issuer-1"))

    async def seed() -> dict[str, str]:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            for p in (alice, boss, approver, support):
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
                c,
                tenant,
                [boss, approver, support],
                ADMIN_ACTIONS,
                ["principal:*", "recovery_case:*"],
            )
            exp = datetime.now(UTC) + timedelta(hours=1)
            return {
                p: await issue_ticket(
                    c,
                    tenant_id=tenant,
                    principal_id=p,
                    purpose="BOOTSTRAP",
                    issued_by="test",
                    expires_at=exp,
                )
                for p in (alice, boss)
            }

    tickets = asyncio.run(seed())
    app = create_app(
        Settings(
            database_url=rt_url,
            token_issuer=ISS,
            token_audience=AUD,
            public_base_url=AUD,
            environment="test",
            webauthn_rp_id=RP,
            webauthn_origins=(ORIGIN,),
        ),
        keyring=ring,
    )

    with TestClient(app, base_url=ISS) as http:

        def post(path: str, body: dict[str, Any], **headers: str) -> Any:
            http.cookies.clear()
            return http.post(
                path, json={"client_id": web, **body}, headers={"Origin": ORIGIN, **headers}
            )

        def register(key: SoftAuthenticator, ticket: str) -> Any:
            o = post("/authn/webauthn/registration/options", {"enrollment_ticket": ticket})
            assert o.status_code == 200, o.text
            return post("/authn/webauthn/registration/verify", {"credential": key.create(o.json())})

        def login(key: SoftAuthenticator) -> Any:
            o = post("/authn/webauthn/authentication/options", {}).json()
            return post("/authn/webauthn/authentication/verify", {"credential": key.get(o)})

        def bearer(sub: str, session: dict[str, Any]) -> dict[str, str]:
            tok = issue_access_token(
                ring,
                issuer=ISS,
                audience=AUD,
                subject=sub,
                tenant_id=tenant,
                client_id=web,
                now=datetime.now(UTC),
                session_id=session["session_id"],
                auth_time=datetime.fromisoformat(session["auth_time"]),
            )
            return {"Authorization": f"Bearer {tok}"}

        alice_key, boss_key = SoftAuthenticator(RP, ORIGIN), SoftAuthenticator(RP, ORIGIN)
        assert register(alice_key, tickets[alice]).status_code == 201
        assert register(boss_key, tickets[boss]).status_code == 201
        alice_session = login(alice_key).json()
        boss_session = login(boss_key).json()

        codes = http.post("/authn/recovery-codes", headers=bearer(alice, alice_session)).json()[
            "codes"
        ]
        boss_codes = http.post("/authn/recovery-codes", headers=bearer(boss, boss_session)).json()[
            "codes"
        ]
        assert len(codes) == 10 and len(set(codes)) == 10

        # ---- RC-1: code -> ticket -> new passkey -> every older session revoked
        r = post("/authn/recovery", {"recovery_code": codes[0].lower().replace("-", "")})
        assert r.status_code == 201, r.text
        rc1 = r.json()
        assert (
            rc1["recovery_class"] == "RC-1"
            and rc1["status"] == "READY"
            and rc1["enrollment_ticket"]
        )
        assert post("/authn/recovery", {"recovery_code": codes[0]}).status_code == 401  # one-time
        new_key = SoftAuthenticator(RP, ORIGIN)
        assert register(new_key, rc1["enrollment_ticket"]).status_code == 201
        assert http.get("/sessions", headers=bearer(alice, alice_session)).status_code == 401
        assert login(new_key).json()["principal_id"] == alice

        # the owner can cancel a recovery they did not start, from a live session
        live = login(new_key).json()
        r = post("/authn/recovery", {"recovery_code": codes[1]})
        hijack = r.json()
        assert (
            http.post(
                f"/authn/recovery/{hijack['recovery_id']}:cancel", headers=bearer(alice, live)
            ).status_code
            == 200
        )
        r = register(SoftAuthenticator(RP, ORIGIN), hijack["enrollment_ticket"])
        assert r.status_code == 409 and r.json()["error"]["code"] == "RECOVERY_NOT_READY"

        # ---- RC-3 (AUTH-13): an administrator's recovery code alone is never enough
        r = post("/authn/recovery", {"recovery_code": boss_codes[0]})
        rc3 = r.json()
        assert rc3["recovery_class"] == "RC-3" and rc3["status"] == "PENDING_REPROOF"
        assert "enrollment_ticket" not in rc3
        handle = rc3["recovery_handle"]

        def advance(**kw: str) -> dict[str, Any]:
            r = post(
                f"/authn/recovery/{rc3['recovery_id']}:continue", {"recovery_handle": handle, **kw}
            )
            assert r.status_code == 200, r.text
            return r.json()

        assert advance()["status"] == "PENDING_REPROOF"
        r = post(f"/authn/recovery/{rc3['recovery_id']}:continue", {"recovery_handle": "x" * 40})
        assert r.status_code == 404  # a wrong handle reveals nothing

        async def reproof() -> None:  # the applicant re-proofs through the verification service
            async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
                pid, _ = await vstore.create_profile(
                    c,
                    tenant_id=tenant,
                    name="recovery",
                    level="IAL-Z2",
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
                    boss,
                    "recovery",
                    "GB",
                    SessionStatus.VERIFIED,
                    "sim",
                    "tx-1",
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
                    (ClaimResult("legal_name", True, "Boss", "doc", ("GOVERNMENT_ID",)),),
                    adapter_id="sim",
                    reasons=("VERIFIED",),
                    evidence_commitment=None,
                    now=now,
                )

        asyncio.run(reproof())
        assert advance()["status"] == "PENDING_APPROVAL"

        # a different administrator approves this exact case through the approval service
        async def strong_session(p: str) -> dict[str, Any]:
            sid, now = uid(), datetime.now(UTC)
            async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
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
            return {"session_id": sid, "auth_time": (now - timedelta(seconds=5)).isoformat()}

        sup = bearer(support, asyncio.run(strong_session(support)))
        tx = transaction_context(rc3["recovery_id"])
        r = http.post(
            "/v1/approval-requests",
            headers=sup,
            json={
                "subject_ref": f"recovery_case:{rc3['recovery_id']}",
                "transaction_context": tx,
                "roles": ["authentication.recovery.approve"],
                "k": 1,
            },
        )
        assert r.status_code == 201, r.text
        approval = r.json()["approval_request_id"]

        async def grant_of(p: str) -> str:
            async with engine(owner_url) as eng, eng.begin() as c:
                return str(
                    (
                        await c.execute(
                            text(
                                "SELECT authority_grant_id FROM authz.authority_grant WHERE grantee_principal_id = :p"
                            ),
                            {"p": uuid.UUID(p)},
                        )
                    ).scalar_one()
                )

        appr = {
            "Authorization": "Bearer "
            + issue_access_token(
                ring,
                issuer=ISS,
                audience=AUD,
                subject=approver,
                tenant_id=tenant,
                client_id=web,
                now=datetime.now(UTC),
            )
        }
        r = http.post(
            f"/v1/approval-requests/{approval}/decisions",
            headers=appr,
            json={
                "verdict": "APPROVE",
                "authority_grant_id": asyncio.run(grant_of(approver)),
                "transaction_context": tx,
            },
        )
        assert r.status_code == 201 and r.json()["state"] == "APPROVED", r.text
        cooling = advance(approval_request_id=approval)
        assert cooling["status"] == "COOLING_OFF" and cooling["cooling_off_until"]
        assert advance()["status"] == "COOLING_OFF"  # the clock does not restart

        async def fast_forward() -> None:
            async with engine(owner_url) as eng, eng.begin() as c:
                await c.execute(
                    text(
                        "UPDATE authn.recovery_case SET cooling_off_until = now() - interval '1 second' "
                        "WHERE recovery_id = :r"
                    ),
                    {"r": uuid.UUID(rc3["recovery_id"])},
                )

        asyncio.run(fast_forward())
        ready = advance()
        assert ready["status"] == "READY" and ready["enrollment_ticket"]
        boss_new = SoftAuthenticator(RP, ORIGIN)
        assert register(boss_new, ready["enrollment_ticket"]).status_code == 201
        assert login(boss_key).status_code == 401  # re-proofed recovery: old factors are LOST
        assert http.get("/sessions", headers=bearer(boss, boss_session)).status_code == 401
        assert login(boss_new).json()["principal_id"] == boss

        # support can open a case (never for themselves) and never complete one
        r = http.post("/admin/recovery-cases", headers=sup, json={"principal_id": support})
        assert r.status_code == 403
        r = http.post("/admin/recovery-cases", headers=sup, json={"principal_id": alice})
        assert (
            r.status_code == 201
            and r.json()["recovery_class"] == "RC-2"
            and r.json()["status"] == "PENDING_REPROOF"
        )
        assist = r.json()
        r = post(
            f"/authn/recovery/{assist['recovery_id']}:continue",
            {"recovery_handle": assist["recovery_handle"]},
        )
        assert r.json()["status"] == "PENDING_REPROOF" and "enrollment_ticket" not in r.json()
        r = http.post(
            f"/admin/recovery-cases/{assist['recovery_id']}:deny",
            headers=sup,
            json={"reason": "not requested"},
        )
        assert r.status_code == 200
        nobody = {
            "Authorization": "Bearer "
            + issue_access_token(
                ring,
                issuer=ISS,
                audience=AUD,
                subject=alice,
                tenant_id=tenant,
                client_id=web,
                now=datetime.now(UTC),
            )
        }
        assert (
            http.post(
                "/admin/recovery-cases", headers=nobody, json={"principal_id": boss}
            ).status_code
            == 403
        )

        # §24 bulk session revoke is itself protected
        target = login(new_key).json()
        assert (
            http.post(f"/admin/principals/{alice}/sessions:revoke", headers=nobody).status_code
            == 403
        )
        r = http.post(f"/admin/principals/{alice}/sessions:revoke", headers=sup)
        assert r.status_code == 200 and r.json()["revoked"] >= 1
        assert http.get("/sessions", headers=bearer(alice, target)).status_code == 401

    async def events() -> dict[str, int]:
        async with engine(owner_url) as eng, eng.begin() as c:
            rows = (
                await c.execute(
                    text(
                        "SELECT event_type, count(*) FROM events.outbox_event WHERE tenant_id = :t AND "
                        "event_type LIKE 'com.zoiko.id.recovery.%' GROUP BY 1"
                    ),
                    {"t": uuid.UUID(tenant)},
                )
            ).all()
        return dict(rows)

    e = asyncio.run(events())
    assert e == {
        "com.zoiko.id.recovery.started.v1": 4,
        "com.zoiko.id.recovery.completed.v1": 2,
        "com.zoiko.id.recovery.denied.v1": 2,
    }, e
