"""SCIM 2.0 provisioning and deprovisioning end to end (ZID-AUTH-001 §11, §18; AUTH-11; AUTH-W6).

A provisioning source bound to one OAuth client creates, filters, patches, replaces and deletes
Users and Groups. `active: false` reaches the user's live session at once (signals path), a
re-activation never restores it, PATCH paths are strict, groups never create authority, and a
source sees only its own resources.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from admin_fixture import make_admin
from conftest import ctx, engine
from fastapi.testclient import TestClient
from scim_fixture import make_scim_client
from sqlalchemy import text
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
from zid_platform.modules.session.domain.session import Session, SessionStatus
from zid_platform.modules.session.infrastructure.session_store import create_session
from zid_platform.modules.session.ports import issue_access_token
from zid_platform.settings import Settings

ISS, AUD = "https://id.zoiko.test", "https://api.zoiko.test"
USER = "urn:ietf:params:scim:schemas:core:2.0:User"
GROUP = "urn:ietf:params:scim:schemas:core:2.0:Group"
PATCH = "urn:ietf:params:scim:api:messages:2.0:PatchOp"


def uid() -> str:
    return str(uuid.uuid4())


def test_scim_lifecycle_and_deprovisioning(rt_url: str, owner_url: str) -> None:
    tenant, admin, svc, other_svc = uid(), uid(), uid(), uid()
    scim_client, other_client = f"scim-{uid()}", f"scim-{uid()}"
    ring = KeyRing(LocalSigner("issuer-1"))

    async def seed() -> str:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            await create_principal(
                c, Principal(admin, tenant, PrincipalType.HUMAN, PrincipalStatus.ACTIVE, "uk-1")
            )
            for s in (svc, other_svc):
                await create_principal(
                    c,
                    Principal(s, tenant, PrincipalType.APPLICATION, PrincipalStatus.ACTIVE, "uk-1"),
                )
            await make_admin(
                c, tenant, admin, ["provisioning.source.manage"], ["provisioning_source:*"]
            )
            for client_id in (scim_client, other_client):
                await make_scim_client(c, tenant, client_id)
            sid, now = uid(), datetime.now(UTC)
            p, _ = await load_principal(c, admin)
            await create_session(
                c,
                Session(
                    sid,
                    tenant,
                    admin,
                    now - timedelta(seconds=10),
                    "native:webauthn",
                    2,
                    p.security_revision,
                    1,
                    SessionStatus.ACTIVE,
                    now + timedelta(hours=1),
                    now,
                ),
            )
            return sid

    admin_sid = asyncio.run(seed())
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

    def bearer(sub: str, client_id: str, scope: str = "", sid: str | None = None) -> dict[str, str]:
        tok = issue_access_token(
            ring,
            issuer=ISS,
            audience=AUD,
            subject=sub,
            tenant_id=tenant,
            client_id=client_id,
            now=datetime.now(UTC),
            session_id=sid,
            scope=frozenset(scope.split()),
        )
        return {"Authorization": f"Bearer {tok}"}

    async def user_session(principal: str) -> str:
        sid, now = uid(), datetime.now(UTC)
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            p, _ = await load_principal(c, principal)
            await create_session(
                c,
                Session(
                    sid,
                    tenant,
                    principal,
                    now - timedelta(seconds=5),
                    "native:webauthn",
                    2,
                    p.security_revision,
                    1,
                    SessionStatus.ACTIVE,
                    now + timedelta(hours=1),
                    now,
                ),
            )
        return sid

    adm = bearer(admin, "console", sid=admin_sid)
    scim = bearer(svc, scim_client, "scim:provision")
    other = bearer(other_svc, other_client, "scim:provision")

    with TestClient(app, base_url=ISS) as http:
        for path in (
            "/scim/v2/ServiceProviderConfig",
            "/scim/v2/ResourceTypes",
            "/scim/v2/Schemas",
        ):
            r = http.get(path)
            assert r.status_code == 200 and r.headers["content-type"].startswith(
                "application/scim+json"
            )
        assert http.get("/scim/v2/ServiceProviderConfig").json()["bulk"]["supported"] is False

        # no source yet: the client is refused; only an admin (R3, real authority) registers one
        assert http.get("/scim/v2/Users", headers=scim).status_code == 403
        assert (
            http.post(
                "/provisioning/sources",
                headers=scim,
                json={"client_id": scim_client, "display_name": "HR"},
            ).status_code
            == 403
        )
        r = http.post(
            "/provisioning/sources",
            headers=adm,
            json={"client_id": scim_client, "display_name": "Workday"},
        )
        assert r.status_code == 201, r.text
        source_id = r.json()["source_id"]
        assert (
            http.post(
                "/provisioning/sources",
                headers=adm,
                json={"client_id": other_client, "display_name": "Other"},
            ).status_code
            == 201
        )

        # create + filter + uniqueness
        body = {
            "schemas": [USER],
            "userName": "bjensen",
            "externalId": "wd-1001",
            "displayName": "Barbara",
            "name": {"givenName": "Barbara", "familyName": "Jensen"},
            "emails": [{"value": "bjensen@acme.example", "primary": True}],
        }
        r = http.post("/scim/v2/Users", headers=scim, json=body)
        assert r.status_code == 201, r.text
        user = r.json()
        uid_ = user["id"]
        assert user["meta"]["version"] == 'W/"1"' and r.headers["etag"] == 'W/"1"'
        r = http.get("/scim/v2/Users", headers=scim, params={"filter": 'userName eq "BJENSEN"'})
        assert r.json()["totalResults"] == 1 and r.json()["Resources"][0]["id"] == uid_
        r = http.post("/scim/v2/Users", headers=scim, json={**body, "externalId": "wd-9"})
        assert r.status_code == 409 and r.json()["scimType"] == "uniqueness"
        assert (
            http.get("/scim/v2/Users", headers=scim, params={"filter": 'emails co "x"'}).status_code
            == 400
        )

        async def principal_of(resource_id: str) -> str:
            async with engine(owner_url) as eng, eng.begin() as c:
                return str(
                    (
                        await c.execute(
                            text(
                                "SELECT principal_id FROM platform.scim_resource WHERE resource_id = :r"
                            ),
                            {"r": uuid.UUID(resource_id)},
                        )
                    ).scalar_one()
                )

        person = asyncio.run(principal_of(uid_))
        live = asyncio.run(user_session(person))
        assert http.get("/sessions", headers=bearer(person, "web", sid=live)).status_code == 200

        # strict PATCH paths and optimistic concurrency
        r = http.patch(
            f"/scim/v2/Users/{uid_}",
            headers=scim,
            json={
                "schemas": [PATCH],
                "Operations": [{"op": "replace", "path": "roles.admin", "value": True}],
            },
        )
        assert r.status_code == 400 and r.json()["scimType"] in ("invalidPath", "invalidValue")
        off = {
            "schemas": [PATCH],
            "Operations": [{"op": "replace", "path": "active", "value": False}],
        }
        r = http.patch(f"/scim/v2/Users/{uid_}", headers={**scim, "If-Match": 'W/"9"'}, json=off)
        assert r.status_code == 412

        # AUTH-11: deprovisioning reaches the live session at once
        r = http.patch(f"/scim/v2/Users/{uid_}", headers={**scim, "If-Match": 'W/"1"'}, json=off)
        assert r.status_code == 200 and r.json()["active"] is False, r.text
        r = http.get("/sessions", headers=bearer(person, "web", sid=live))
        assert r.status_code == 401

        # re-activation restores the principal, never the old session
        on = {
            "schemas": [PATCH],
            "Operations": [{"op": "replace", "path": "active", "value": True}],
        }
        assert http.patch(f"/scim/v2/Users/{uid_}", headers=scim, json=on).json()["active"] is True
        assert http.get("/sessions", headers=bearer(person, "web", sid=live)).status_code == 401
        fresh = asyncio.run(user_session(person))
        assert http.get("/sessions", headers=bearer(person, "web", sid=fresh)).status_code == 200

        # PUT replaces; another source cannot see or touch this resource
        r = http.put(
            f"/scim/v2/Users/{uid_}", headers=scim, json={**body, "displayName": "Barbara J."}
        )
        assert r.status_code == 200 and r.json()["displayName"] == "Barbara J."
        assert http.get(f"/scim/v2/Users/{uid_}", headers=other).status_code == 404
        assert http.get("/scim/v2/Users", headers=other).json()["totalResults"] == 0
        assert (
            http.get("/scim/v2/Users", headers=bearer(svc, scim_client)).status_code == 403
        )  # no scope

        # groups: membership facts only
        second = http.post(
            "/scim/v2/Users", headers=scim, json={"schemas": [USER], "userName": "jsmith"}
        ).json()["id"]
        r = http.post(
            "/scim/v2/Groups",
            headers=scim,
            json={"schemas": [GROUP], "displayName": "Admins", "members": [{"value": uid_}]},
        )
        assert r.status_code == 201, r.text
        gid = r.json()["id"]
        r = http.patch(
            f"/scim/v2/Groups/{gid}",
            headers=scim,
            json={
                "schemas": [PATCH],
                "Operations": [
                    {"op": "add", "path": "members", "value": [{"value": second}]},
                    {"op": "remove", "path": f'members[value eq "{uid_}"]'},
                ],
            },
        )
        assert r.status_code == 200, r.text
        assert [m["value"] for m in r.json()["members"]] == [second]
        foreign = http.post(
            "/scim/v2/Users", headers=other, json={"schemas": [USER], "userName": "zz"}
        ).json()["id"]
        r = http.patch(
            f"/scim/v2/Groups/{gid}",
            headers=scim,
            json={
                "schemas": [PATCH],
                "Operations": [{"op": "add", "path": "members", "value": [{"value": foreign}]}],
            },
        )
        assert r.status_code == 404  # a member from another source is unknown here

        async def grants() -> int:
            async with engine(owner_url) as eng, eng.begin() as c:
                return int(
                    (
                        await c.execute(
                            text("SELECT count(*) FROM authz.authority_grant WHERE tenant_id = :t"),
                            {"t": uuid.UUID(tenant)},
                        )
                    ).scalar_one()
                )

        assert (
            asyncio.run(grants()) == 1
        )  # only the admin fixture's grant: SCIM groups grant nothing

        # delete = deprovision; suspended source = refused
        assert http.delete(f"/scim/v2/Users/{second}", headers=scim).status_code == 204
        assert http.get(f"/scim/v2/Users/{second}", headers=scim).status_code == 404
        r = http.post(f"/provisioning/sources/{source_id}:suspend", headers=adm)
        assert r.status_code == 200 and r.json()["status"] == "SUSPENDED"
        assert http.get("/scim/v2/Users", headers=scim).status_code == 403

    async def states() -> dict[str, Any]:
        async with engine(owner_url) as eng, eng.begin() as c:
            rows = (
                await c.execute(
                    text(
                        "SELECT r.user_name, p.status, s.state FROM platform.scim_resource r "
                        "JOIN core.principal p ON p.principal_id = r.principal_id "
                        "LEFT JOIN signals.security_state s ON s.subject_id = p.principal_id::text "
                        "WHERE r.tenant_id = :t AND r.resource_type = 'User'"
                    ),
                    {"t": uuid.UUID(tenant)},
                )
            ).all()
        return {r.user_name: (r.status, r.state) for r in rows}

    assert asyncio.run(states()) == {
        "bjensen": ("ACTIVE", "REAUTH_REQUIRED"),
        "jsmith": ("SUSPENDED", "SUSPENDED"),
        "zz": ("ACTIVE", None),
    }
