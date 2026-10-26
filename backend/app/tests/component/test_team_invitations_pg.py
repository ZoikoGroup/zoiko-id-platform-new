"""Team invitations as the tenant console shows them (G-W0 Team page; OVERLAY-001 §4).

An administrator invites a person and gets a single-use passkey setup link. The members list then
follows that person: INVITED -> PASSKEY_CREATED -> SIGNED_IN -> ACTIVE (they joined). A new link
can be issued only while no passkey exists; removing a never-joined invitee disables the account,
so the old link can no longer enroll a passkey.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, datetime
from typing import Any

from conftest import ctx, engine
from fastapi.testclient import TestClient
from onboarding_fixture import AUD, ISS, _session, onboard
from zid_db import tenant_transaction
from zid_platform.main import create_app
from zid_platform.modules.authentication.domain.authenticator import (
    Authenticator,
    AuthenticatorStatus,
)
from zid_platform.modules.authentication.infrastructure.authenticator_store import (
    add_authenticator,
    peek_ticket,
)
from zid_platform.modules.principal.infrastructure.principal_store import load_principal
from zid_platform.settings import Settings


def test_invite_share_and_follow_progress(rt_url: str) -> None:
    o = onboard(rt_url)
    app = create_app(
        Settings(
            database_url=rt_url,
            token_issuer=ISS,
            token_audience=AUD,
            public_base_url=AUD,
            environment="test",
            cell_id="uk-1",
        ),
        keyring=o.ring,
    )

    def progress(api: TestClient, principal: str) -> dict[str, Any]:
        items = api.get("/v1/tenant/members", headers=o.bearer(o.owner)).json()["items"]
        return next(m for m in items if m["principal_id"] == principal)

    async def passkey(principal: str) -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(o.tenant)) as c:
            await add_authenticator(
                c,
                Authenticator(
                    str(uuid.uuid4()),
                    o.tenant,
                    principal,
                    "PASSKEY",
                    AuthenticatorStatus.ACTIVE,
                    datetime.now(UTC),
                    credential_id=os.urandom(32),
                    public_key_cose=b"\x01" * 77,
                    user_verified=True,
                ),
                aal=2,
            )

    async def sign_in(principal: str) -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(o.tenant)) as c:
            o.sessions[principal] = await _session(c, o.tenant, principal)

    with TestClient(app) as api:
        r = api.post("/v1/tenant/members", json={"role": "MEMBER"}, headers=o.bearer(o.owner))
        assert r.status_code == 201, r.text
        inv = r.json()
        person, ticket = inv["principal_id"], inv["enrollment_ticket"]
        m = progress(api, person)
        assert (m["progress"], m["passkeys"], m["last_sign_in_at"]) == ("INVITED", 0, None)

        # the link was lost: a fresh one, while no passkey exists
        r = api.post(
            f"/v1/tenant/members/{inv['assignment_id']}:reissue", headers=o.bearer(o.owner)
        )
        assert r.status_code == 200 and r.json()["enrollment_ticket"] not in ("", ticket)

        asyncio.run(passkey(person))
        m = progress(api, person)
        assert (
            m["progress"] == "PASSKEY_CREATED" and m["passkeys"] == 1 and m["passkey_registered_at"]
        )
        r = api.post(
            f"/v1/tenant/members/{inv['assignment_id']}:reissue", headers=o.bearer(o.owner)
        )
        assert r.status_code == 409  # they sign in with their passkey now; no more links

        asyncio.run(sign_in(person))
        assert progress(api, person)["progress"] == "SIGNED_IN"
        r = api.post("/v1/tenant/members/me:accept", headers=o.bearer(person))
        assert r.status_code == 200 and r.json()["status"] == "ACTIVE"
        m = progress(api, person)
        assert m["progress"] == "ACTIVE" and m["last_sign_in_at"]

        # removing someone who never joined also stops their link from enrolling a passkey
        r = api.post("/v1/tenant/members", json={"role": "ADMIN"}, headers=o.bearer(o.owner))
        other = r.json()
        r = api.post(
            f"/v1/tenant/members/{other['assignment_id']}:revoke", headers=o.bearer(o.owner)
        )
        assert r.status_code == 200 and r.json()["status"] == "REVOKED"
        assert progress(api, other["principal_id"])["progress"] == "REVOKED"

    async def disabled() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(o.tenant)) as c:
            p, _ = await load_principal(c, other["principal_id"])
            assert p.status.value == "DISABLED"
            assert await peek_ticket(c, other["enrollment_ticket"], datetime.now(UTC)) is not None
            # (the ticket row remains, but registration requires an ACTIVE principal)

    asyncio.run(disabled())


def test_an_invited_administrator_becomes_a_second_approver(rt_url: str) -> None:
    """OVERLAY §4: accepting an Administrator invitation gives the person the owner's delegated tenant
    administration, so they can approve the owner's onboarding step; a Member gets nothing."""
    o = onboard(rt_url, delegable=True)
    app = create_app(
        Settings(
            database_url=rt_url,
            token_issuer=ISS,
            token_audience=AUD,
            public_base_url=AUD,
            environment="test",
            cell_id="uk-1",
        ),
        keyring=o.ring,
    )

    async def join(principal: str) -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(o.tenant)) as c:
            await add_authenticator(
                c,
                Authenticator(
                    str(uuid.uuid4()), o.tenant, principal, "PASSKEY", AuthenticatorStatus.ACTIVE,
                    datetime.now(UTC), credential_id=os.urandom(32), public_key_cose=b"\x01" * 77,
                    user_verified=True,
                ),
                aal=2,
            )  # fmt: skip
            o.sessions[principal] = await _session(c, o.tenant, principal)

    async def grants_of(principal: str) -> list[Any]:
        from zid_platform.modules.authority.ports import load_grants_for_grantee

        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(o.tenant)) as c:
            return await load_grants_for_grantee(c, principal)

    with TestClient(app) as api:
        admin = api.post(
            "/v1/tenant/members", json={"role": "ADMIN"}, headers=o.bearer(o.owner)
        ).json()
        member = api.post(
            "/v1/tenant/members", json={"role": "MEMBER"}, headers=o.bearer(o.owner)
        ).json()
        for who in (admin, member):
            asyncio.run(join(who["principal_id"]))
            r = api.post("/v1/tenant/members/me:accept", headers=o.bearer(who["principal_id"]))
            assert r.status_code == 200, r.text

        assert asyncio.run(grants_of(member["principal_id"])) == []  # a Member holds no authority
        [grant] = asyncio.run(grants_of(admin["principal_id"]))
        assert grant.parent_grant_id is not None and grant.grantor_principal_id == o.owner
        assert "tenant.onboarding.manage" in grant.authority.actions
        assert grant.max_delegation_depth == 0  # an Administrator cannot hand it on

        ctx_line = "tenant.onboarding.manage|onboarding_plan:plan-1|APPROVE"
        r = api.post(
            "/v1/approval-requests",
            json={"subject_ref": "onboarding_plan:plan-1", "transaction_context": ctx_line,
                  "mode": "SINGLE", "roles": ["tenant.onboarding.manage"], "k": 1},
            headers=o.bearer(o.owner),
        )  # fmt: skip
        assert r.status_code == 201, r.text
        rid = r.json()["approval_request_id"]
        r = api.post(
            f"/v1/approval-requests/{rid}/decisions",
            json={
                "verdict": "APPROVE",
                "authority_grant_id": grant.grant_id,
                "transaction_context": ctx_line,
            },
            headers=o.bearer(admin["principal_id"]),
        )
        assert r.status_code == 201 and r.json()["state"] == "APPROVED", r.text
        # the owner cannot approve their own request
        own = asyncio.run(grants_of(o.owner))
        r = api.post(
            f"/v1/approval-requests/{rid}/decisions",
            json={
                "verdict": "APPROVE",
                "authority_grant_id": own[0].grant_id,
                "transaction_context": ctx_line,
            },
            headers=o.bearer(o.owner),
        )
        assert (
            r.status_code >= 400 or r.json().get("counted_approvers") == 1
        )  # still one approver: not counted twice


def test_guided_approval_needs_no_ids_or_context_strings(rt_url: str) -> None:
    """The owner submits a change with a plain summary; the Administrator finds it waiting, sees what it is, and
    approves with one call that names neither a grant nor a context; the owner then sees it approved."""
    o = onboard(rt_url, delegable=True, extra_resources=["approval_request:*"])
    app = create_app(
        Settings(
            database_url=rt_url, token_issuer=ISS, token_audience=AUD, public_base_url=AUD,
            environment="test", cell_id="uk-1",
        ),
        keyring=o.ring,
    )  # fmt: skip

    async def join(principal: str) -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(o.tenant)) as c:
            await add_authenticator(
                c,
                Authenticator(
                    str(uuid.uuid4()), o.tenant, principal, "PASSKEY", AuthenticatorStatus.ACTIVE,
                    datetime.now(UTC), credential_id=os.urandom(32), public_key_cose=b"\x01" * 77,
                    user_verified=True,
                ),
                aal=2,
            )  # fmt: skip
            o.sessions[principal] = await _session(c, o.tenant, principal)

    change = {
        "action": "tenant.residency.manage",
        "resource": "tenant_residency",
        "change": "SRP-1|local-1|||uk-gdpr",
    }
    with TestClient(app) as api:
        admin = api.post(
            "/v1/tenant/members", json={"role": "ADMIN"}, headers=o.bearer(o.owner)
        ).json()
        asyncio.run(join(admin["principal_id"]))
        assert (
            api.post(
                "/v1/tenant/members/me:accept", headers=o.bearer(admin["principal_id"])
            ).status_code
            == 200
        )

        raw = lambda: api.get(
            "/v1/approval-requests/for-change", params=change, headers=o.bearer(o.owner)
        )
        mine = lambda: raw().json()["request"]
        assert mine() is None  # nothing asked yet
        r = api.post(
            "/v1/approval-requests",
            json={"subject_ref": "tenant_residency", "roles": ["tenant.residency.manage"],
                  "summary": "Set residency to Regional resident, home cell local-1", **change},
            headers=o.bearer(o.owner),
        )  # fmt: skip
        assert r.status_code == 201, r.text
        rid = r.json()["approval_request_id"]
        assert (
            mine()["state"] == "PENDING" and mine()["approval_request_id"] == rid
        )  # found again after a reload

        # the requester is never asked to approve their own request
        assert (
            api.get("/v1/approval-requests/awaiting-me", headers=o.bearer(o.owner)).json()["items"]
            == []
        )
        waiting = api.get(
            "/v1/approval-requests/awaiting-me", headers=o.bearer(admin["principal_id"])
        ).json()["items"]
        assert [w["approval_request_id"] for w in waiting] == [rid]
        assert (
            waiting[0]["summary"].startswith("Set residency") and waiting[0]["authority_grant_id"]
        )

        r = api.post(
            f"/v1/approval-requests/{rid}/decisions", json={"verdict": "APPROVE"},
            headers=o.bearer(admin["principal_id"]),
        )  # fmt: skip
        assert r.status_code == 201 and r.json()["state"] == "APPROVED", r.text
        assert mine()["state"] == "APPROVED"
        # decided, so it no longer waits for them
        assert (
            api.get(
                "/v1/approval-requests/awaiting-me", headers=o.bearer(admin["principal_id"])
            ).json()["items"]
            == []
        )

        # a change to any part of the request is a different request: nothing is found for it
        other = {**change, "change": "SRP-1|local-1|||eu-gdpr"}
        assert (
            api.get(
                "/v1/approval-requests/for-change", params=other, headers=o.bearer(o.owner)
            ).json()["request"]
            is None
        )
