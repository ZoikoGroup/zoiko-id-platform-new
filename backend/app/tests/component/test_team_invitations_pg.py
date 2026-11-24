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


# ---------------------------------------------------------------------------------------------------- the invitation by email
#
# The fixed template ZID-EM-004 ("Invitation to join an organization") is sent to the address the inviter typed: with the invitation, or afterwards on request.
# A mail problem never loses the link, and the email carries one token that the /join page turns into the passkey setup address.

from test_email_pg import FakeSender, make_cfg


def _app_with_email(rt_url: str, o: Any, *, sender: Any = None, configured: bool = True) -> tuple[TestClient, Any]:
    app = create_app(
        Settings(database_url=rt_url, token_issuer=ISS, token_audience=AUD, public_base_url=AUD, environment="test", cell_id="uk-1"),
        keyring=o.ring,
    )  # fmt: skip
    sender = sender or FakeSender()
    if configured:
        app.state.email_config = make_cfg()
        app.state.email_sender = sender
    return TestClient(app, base_url=ISS), sender


def test_inviting_with_an_email_sends_the_fixed_template_and_still_returns_the_link(rt_url: str) -> None:
    o = onboard(rt_url)
    api, sender = _app_with_email(rt_url, o)
    with api:
        res = api.post("/v1/tenant/members", headers=o.bearer(o.owner), json={"role": "OPERATOR", "email": "Asha.Rao@Example.com", "send_email": True})
        assert res.status_code == 201, res.text
        body = res.json()
        assert body["enrollment_ticket"]  # the link is still handed back
        assert body["email"]["status"] == "SENT" and body["email"]["to"] == "a***@example.com"  # the address is never echoed whole
    assert len(sender.sent) == 1
    to, rendered, _ = sender.sent[0]
    assert to == "asha.rao@example.com"
    assert rendered.template_id == "ZID-EM-004"
    assert "invited you to join" in rendered.text and "an Operator" in rendered.text  # fixed words, merged role
    assert "/r/single_use_invitation_url?t=" in rendered.text  # the link is the one routed token
    token = rendered.text.split("?t=")[1].split()[0].strip(".")
    slug, _, ticket = token.partition("_")
    assert ticket == body["enrollment_ticket"] and slug  # <workspace>_<ticket>


def test_inviting_without_asking_for_an_email_sends_nothing(rt_url: str) -> None:
    o = onboard(rt_url)
    api, sender = _app_with_email(rt_url, o)
    with api:
        res = api.post("/v1/tenant/members", headers=o.bearer(o.owner), json={"role": "VIEWER", "email": "someone@example.com"})
        assert res.status_code == 201 and "email" not in res.json()  # the address alone only lets sign-up recognise the person
    assert sender.sent == []


def test_an_existing_invitation_can_be_emailed_later_with_a_fresh_single_use_link(rt_url: str) -> None:
    o = onboard(rt_url)
    api, sender = _app_with_email(rt_url, o)
    with api:
        made = api.post("/v1/tenant/members", headers=o.bearer(o.owner), json={"role": "OPERATOR"}).json()
        again = api.post(f"/v1/tenant/members/{made['assignment_id']}:email-invitation", headers=o.bearer(o.owner), json={"email": "new.person@example.com"})
        assert again.status_code == 200, again.text
        body = again.json()
        assert body["email"]["status"] == "SENT"
        assert body["enrollment_ticket"] != made["enrollment_ticket"]  # the link on screen before is replaced by this one
    assert [t for t, _, _ in sender.sent] == ["new.person@example.com"]
    assert body["enrollment_ticket"] in sender.sent[0][1].text


def test_a_bad_address_is_reported_and_nothing_is_sent(rt_url: str) -> None:
    o = onboard(rt_url)
    api, sender = _app_with_email(rt_url, o)
    with api:
        made = api.post("/v1/tenant/members", headers=o.bearer(o.owner), json={"role": "OPERATOR"}).json()
        for bad in ("not-an-address", "a@b.com, c@d.com", "Name <a@b.com>", "a@b.com\r\nBcc: x@y.com"):
            res = api.post(f"/v1/tenant/members/{made['assignment_id']}:email-invitation", headers=o.bearer(o.owner), json={"email": bad})
            assert res.status_code == 200 and res.json()["email"]["status"] == "INVALID_ADDRESS", bad
    assert sender.sent == []  # no header smuggling, no second recipient


def test_when_email_is_not_set_up_the_invitation_is_still_made_and_the_answer_says_so(rt_url: str) -> None:
    o = onboard(rt_url)
    api, _ = _app_with_email(rt_url, o, configured=False)
    with api:
        res = api.post("/v1/tenant/members", headers=o.bearer(o.owner), json={"role": "OPERATOR", "email": "a@example.com", "send_email": True})
        assert res.status_code == 201
        assert res.json()["email"]["status"] == "NOT_CONFIGURED" and res.json()["enrollment_ticket"]


def test_a_delivery_failure_never_loses_the_link(rt_url: str) -> None:
    from zid_platform.modules.communication.infrastructure.smtp import SendResult

    o = onboard(rt_url)
    api, _ = _app_with_email(rt_url, o, sender=FakeSender([SendResult("REJECTED", "550", "x")] * 6))
    with api:
        res = api.post("/v1/tenant/members", headers=o.bearer(o.owner), json={"role": "OPERATOR", "email": "a@example.com", "send_email": True})
        assert res.status_code == 201 and res.json()["enrollment_ticket"]  # the invitation exists whatever the mail did
        assert res.json()["email"]["status"] != "SENT"


def test_only_someone_who_may_invite_can_email_an_invitation(rt_url: str) -> None:
    o = onboard(rt_url)
    api, sender = _app_with_email(rt_url, o)
    with api:
        made = api.post("/v1/tenant/members", headers=o.bearer(o.owner), json={"role": "OPERATOR"}).json()
        outsider = o.bearer(str(uuid.uuid4()))
        res = api.post(f"/v1/tenant/members/{made['assignment_id']}:email-invitation", headers=outsider, json={"email": "a@example.com"})
        assert res.status_code in (401, 403)
    assert sender.sent == []
