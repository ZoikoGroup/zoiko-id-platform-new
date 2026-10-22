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
