"""SCIM field-by-field: every attribute a source sends is kept, read back, replaced or cleared as RFC
7643/7644 say, and the connector/lifecycle surfaces report the state they caused (AUTH-W6, G-W5)."""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from admin_fixture import make_admin
from conftest import ctx, engine
from fastapi.testclient import TestClient
from scim_fixture import make_scim_client
from sqlalchemy import text
from zid_crypto import KeyRing, LocalSigner, sign_jws, verify_jws
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
from zid_platform.modules.session.ports import issue_access_token, load_client, set_client_status
from zid_platform.settings import Settings

ISS, AUD = "https://id.zoiko.test", "https://api.zoiko.test"
USER = "urn:ietf:params:scim:schemas:core:2.0:User"
GROUP = "urn:ietf:params:scim:schemas:core:2.0:Group"
PATCH = "urn:ietf:params:scim:api:messages:2.0:PatchOp"
ENTERPRISE = "urn:ietf:params:scim:schemas:extension:enterprise:2.0:User"


def uid() -> str:
    return str(uuid.uuid4())


def test_scim_fields_round_trip(rt_url: str, owner_url: str) -> None:
    tenant, admin, svc = uid(), uid(), uid()
    client = f"scim-{uid()}"
    ring = KeyRing(LocalSigner("issuer-1"))

    async def seed() -> str:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            await create_principal(
                c, Principal(admin, tenant, PrincipalType.HUMAN, PrincipalStatus.ACTIVE, "uk-1")
            )
            await create_principal(
                c,
                Principal(svc, tenant, PrincipalType.APPLICATION, PrincipalStatus.ACTIVE, "uk-1"),
            )
            await make_admin(
                c,
                tenant,
                admin,
                ["provisioning.source.manage", "provisioning.source.read"],
                ["provisioning_source:*"],
            )
            await make_scim_client(c, tenant, client)
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

    adm = bearer(admin, "console", sid=admin_sid)
    scim = bearer(svc, client, "scim:provision")

    async def row(resource_id: str) -> Any:
        async with engine(owner_url) as eng, eng.begin() as c:
            return (
                await c.execute(
                    text("SELECT * FROM platform.scim_resource WHERE resource_id = :r"),
                    {"r": uuid.UUID(resource_id)},
                )
            ).one()

    async def oauth_client(client_id: str) -> Any:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            return await load_client(c, client_id)

    async def seed_client(for_tenant: str, client_id: str, scope: str = "scim:provision") -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(for_tenant)) as c:
            await make_scim_client(c, for_tenant, client_id, scope=scope)

    async def retire(client_id: str) -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            await set_client_status(c, client_id, "RETIRED")

    with TestClient(app, base_url=ISS) as http:
        # ---- sources: every request field is stored and every stored field is returned
        r = http.post(
            "/provisioning/sources",
            headers=adm,
            json={"client_id": f"  {client} ", "display_name": "  Workday HR  "},
        )
        assert r.status_code == 201, r.text
        src = r.json()
        assert src["client_id"] == client and src["display_name"] == "Workday HR"
        assert src["status"] == "ACTIVE" and src["client_created"] is False
        assert src["scim_base_url"].endswith("/scim/v2")
        assert src["token_endpoint"].endswith("/oauth2/token")
        view = {k: src[k] for k in ("source_id", "client_id", "display_name", "status")}
        blank = http.post(
            "/provisioning/sources", headers=adm, json={"client_id": "x", "display_name": "   "}
        )
        assert blank.status_code in (400, 422)
        dup = http.post(
            "/provisioning/sources",
            headers=adm,
            json={"client_id": client, "display_name": "Again"},
        )
        assert dup.status_code == 409, dup.text
        assert "PROVISIONING_SOURCE_EXISTS" in dup.text
        assert http.get("/provisioning/sources", headers=adm).json()["items"] == [view]

        # ---- user: create with every attribute, read back, DB row
        body = {
            "userName": "BJensen",
            "externalId": "wd-1001",
            "displayName": "Barbara Jensen",
            "name": {"givenName": "Barbara", "familyName": "Jensen", "formatted": "Ms. B Jensen"},
            "emails": [
                {"value": "bjensen@acme.example", "type": "work", "primary": True},
                {"value": "babs@home.example", "type": "home"},
            ],
            "active": True,
            "nickName": "Babs",
            "title": "Engineer",
            "userType": "Employee",
            "preferredLanguage": "en",
            "locale": "en-GB",
            "timezone": "Europe/London",
            "profileUrl": "https://people.acme.example/bjensen",
            "phoneNumbers": [{"value": "+44 20 7946 0000", "type": "work"}],
            "addresses": [
                {"streetAddress": "1 Mill St", "locality": "Leeds", "country": "GB", "type": "work"}
            ],
            "roles": [{"value": "admin"}],  # never stored: SCIM carries profile data, not authority
            "entitlements": [{"value": "billing"}],
            "schemas": [USER, ENTERPRISE],
            ENTERPRISE: {
                "employeeNumber": "E-1001",
                "department": "Platform",
                "organization": "Acme",
                "costCenter": "CC-7",
                "division": "Eng",
            },
        }
        r = http.post("/scim/v2/Users", headers=scim, json=body)
        assert r.status_code == 201, r.text
        u = r.json()
        assert u["userName"] == "BJensen" and u["externalId"] == "wd-1001"
        assert u["displayName"] == "Barbara Jensen" and u["active"] is True
        assert u["name"]["givenName"] == "Barbara" and u["name"]["formatted"] == "Ms. B Jensen"
        assert {e["value"] for e in u["emails"]} == {"bjensen@acme.example", "babs@home.example"}
        assert next(e for e in u["emails"] if e.get("primary"))["type"] == "work"
        for k in ("nickName", "title", "userType", "preferredLanguage", "locale", "timezone"):
            assert u[k] == body[k], k
        assert u["profileUrl"] == body["profileUrl"]
        assert u["phoneNumbers"][0]["value"] == "+44 20 7946 0000"
        assert u["addresses"][0]["locality"] == "Leeds" and u["addresses"][0]["country"] == "GB"
        assert (
            u[ENTERPRISE]["employeeNumber"] == "E-1001"
            and u[ENTERPRISE]["department"] == "Platform"
        )
        assert ENTERPRISE in u["schemas"] and "roles" not in u and "entitlements" not in u
        assert u["meta"]["resourceType"] == "User" and u["meta"]["version"] == 'W/"1"'
        assert u["meta"]["location"].endswith(f"/scim/v2/Users/{u['id']}")
        db = asyncio.run(row(u["id"]))
        assert (db.user_name, db.external_id, db.display_name, db.active, db.version) == (
            "BJensen",
            "wd-1001",
            "Barbara Jensen",
            True,
            1,
        )
        assert db.attributes["name"]["familyName"] == "Jensen" and len(db.attributes["emails"]) == 2
        assert db.attributes["title"] == "Engineer" and db.attributes["timezone"] == "Europe/London"
        assert db.attributes["phoneNumbers"][0]["type"] == "work"
        assert db.attributes["addresses"][0]["streetAddress"] == "1 Mill St"
        assert db.attributes[ENTERPRISE]["costCenter"] == "CC-7"
        assert not {"roles", "entitlements", "groups", "password"} & db.attributes.keys()
        assert http.get(f"/scim/v2/Users/{u['id']}", headers=scim).json() == u

        # ---- filters on every supported attribute
        for flt in ('externalId eq "wd-1001"', 'displayName eq "Barbara Jensen"'):
            r = http.get("/scim/v2/Users", headers=scim, params={"filter": flt})
            assert r.json()["totalResults"] == 1, flt
        r = http.get("/scim/v2/Users", headers=scim, params={"filter": 'externalId eq "nope"'})
        assert r.json()["totalResults"] == 0 and r.json()["Resources"] == []

        # ---- over-long values are a 400, not a database error
        long = http.post("/scim/v2/Users", headers=scim, json={**body, "userName": "x" * 300})
        assert long.status_code == 400 and long.json()["scimType"] == "invalidValue"

        # ---- PUT replaces: omitted externalId / emails / name are cleared
        r = http.put(
            f"/scim/v2/Users/{u['id']}",
            headers={**scim, "If-Match": '"1"'},  # a strong tag is accepted too
            json={"schemas": [USER], "userName": "bjensen", "displayName": "B. Jensen"},
        )
        assert r.status_code == 200, r.text
        p = r.json()
        assert p["meta"]["version"] == 'W/"2"' and r.headers["etag"] == 'W/"2"'
        assert p["displayName"] == "B. Jensen" and p["active"] is True
        assert "externalId" not in p and "emails" not in p and "name" not in p
        assert not {"title", "phoneNumbers", "addresses", ENTERPRISE} & p.keys()
        db = asyncio.run(row(u["id"]))
        assert db.external_id is None and db.attributes == {} and db.version == 2

        # ---- PATCH the new attributes: add / replace / remove, including the enterprise extension
        r = http.patch(
            f"/scim/v2/Users/{u['id']}",
            headers=scim,
            json={
                "schemas": [PATCH],
                "Operations": [
                    {"op": "add", "path": "title", "value": "Lead"},
                    {"op": "replace", "path": "timezone", "value": "UTC"},
                    {"op": "add", "path": f"{ENTERPRISE}:department", "value": "Security"},
                    {
                        "op": "add",
                        "path": "phoneNumbers",
                        "value": [{"value": "+1 555", "type": "mobile"}],
                    },
                ],
            },
        )
        assert r.status_code == 200, r.text
        pu = r.json()
        assert pu["title"] == "Lead" and pu["timezone"] == "UTC"
        assert (
            pu[ENTERPRISE]["department"] == "Security" and pu["phoneNumbers"][0]["type"] == "mobile"
        )
        r = http.patch(
            f"/scim/v2/Users/{u['id']}",
            headers=scim,
            json={
                "schemas": [PATCH],
                "Operations": [
                    {"op": "remove", "path": "title"},
                    {"op": "remove", "path": f"{ENTERPRISE}:department"},
                ],
            },
        )
        assert r.status_code == 200, r.text
        assert "title" not in r.json() and ENTERPRISE not in r.json()
        # roles can never be reached through SCIM: not by PATCH, not by PUT, not by create
        for op in (
            {"op": "add", "path": "roles", "value": [{"value": "admin"}]},
            {"op": "replace", "path": "entitlements", "value": [{"value": "billing"}]},
        ):
            r = http.patch(
                f"/scim/v2/Users/{u['id']}",
                headers=scim,
                json={"schemas": [PATCH], "Operations": [op]},
            )
            assert r.status_code in (200, 400), r.text
            assert "roles" not in r.json() and "entitlements" not in r.json()
        assert not {"roles", "entitlements"} & asyncio.run(row(u["id"])).attributes.keys()
        too_long = {**body, "userName": "x2", "title": "t" * 300, "externalId": None}
        r = http.post("/scim/v2/Users", headers=scim, json=too_long)
        assert r.status_code == 400 and r.json()["scimType"] == "invalidValue", r.text
        many = {**body, "userName": "x3", "externalId": None, "phoneNumbers": [{"value": "1"}] * 25}
        assert http.post("/scim/v2/Users", headers=scim, json=many).status_code == 400
        orphan = {
            **body,
            "userName": "x4",
            "externalId": None,
            ENTERPRISE: {"manager": {"value": uid()}},
        }
        assert http.post("/scim/v2/Users", headers=scim, json=orphan).status_code == 400
        boss = {
            "schemas": [USER, ENTERPRISE],
            "userName": "x5",
            ENTERPRISE: {"manager": {"value": u["id"]}},
        }
        r = http.post("/scim/v2/Users", headers=scim, json=boss)
        assert r.status_code == 201, r.text
        mgr = r.json()[ENTERPRISE]["manager"]
        assert mgr["value"] == u["id"] and mgr["$ref"].endswith(f"/scim/v2/Users/{u['id']}")
        assert http.delete(f"/scim/v2/Users/{r.json()['id']}", headers=scim).status_code == 204
        sch = http.get("/scim/v2/Schemas", headers=scim).json()
        assert {x["id"] for x in sch["Resources"]} >= {USER, ENTERPRISE, GROUP}

        # ---- PATCH: add attributes, then remove one; ETag list; stale tag is 412
        cur = http.get(f"/scim/v2/Users/{u['id']}", headers=scim).json()["meta"]["version"]
        r = http.patch(
            f"/scim/v2/Users/{u['id']}",
            headers={**scim, "If-Match": f'W/"1", {cur}'},
            json={
                "schemas": [PATCH],
                "Operations": [
                    {"op": "add", "path": "externalId", "value": "wd-2"},
                    {"op": "replace", "path": "name.givenName", "value": "Babs"},
                ],
            },
        )
        assert r.status_code == 200, r.text
        assert r.json()["externalId"] == "wd-2" and r.json()["name"]["givenName"] == "Babs"
        r = http.patch(
            f"/scim/v2/Users/{u['id']}",
            headers=scim,
            json={"schemas": [PATCH], "Operations": [{"op": "remove", "path": "externalId"}]},
        )
        assert r.status_code == 200 and "externalId" not in r.json(), r.text
        assert asyncio.run(row(u["id"])).external_id is None
        off_op = {"op": "replace", "path": "active", "value": False}
        r = http.patch(
            f"/scim/v2/Users/{u['id']}",
            headers={**scim, "If-Match": 'W/"1"'},
            json={"schemas": [PATCH], "Operations": [off_op]},
        )
        assert r.status_code == 412

        # ---- groups: create / PUT / PATCH (pathless too), user.groups, member display
        second = http.post(
            "/scim/v2/Users", headers=scim, json={"schemas": [USER], "userName": "jsmith"}
        ).json()["id"]
        r = http.post(
            "/scim/v2/Groups",
            headers=scim,
            json={
                "schemas": [GROUP],
                "displayName": "Admins",
                "externalId": "g-1",
                "members": [{"value": u["id"]}],
            },
        )
        assert r.status_code == 201, r.text
        g = r.json()
        assert g["externalId"] == "g-1" and g["meta"]["version"] == 'W/"1"'
        assert g["members"][0]["value"] == u["id"] and g["members"][0]["display"] == "bjensen"
        assert asyncio.run(row(g["id"])).external_id == "g-1"
        got = http.get(f"/scim/v2/Users/{u['id']}", headers=scim).json()
        assert [(x["value"], x["display"], x["type"]) for x in got["groups"]] == [
            (g["id"], "Admins", "direct")
        ]
        r = http.put(
            f"/scim/v2/Groups/{g['id']}",
            headers={**scim, "If-Match": 'W/"1"'},
            json={"schemas": [GROUP], "displayName": "Owners", "members": [{"value": second}]},
        )
        assert r.status_code == 200, r.text
        pg = r.json()
        assert pg["displayName"] == "Owners" and "externalId" not in pg
        assert [m["value"] for m in pg["members"]] == [second]
        assert pg["meta"]["version"] == 'W/"2"'
        assert "groups" not in http.get(f"/scim/v2/Users/{u['id']}", headers=scim).json()
        r = http.patch(
            f"/scim/v2/Groups/{g['id']}",
            headers=scim,
            json={
                "schemas": [PATCH],
                "Operations": [
                    {"op": "replace", "value": {"displayName": "Staff", "externalId": "g-2"}}
                ],
            },
        )
        assert r.status_code == 200, r.text
        assert r.json()["displayName"] == "Staff" and r.json()["externalId"] == "g-2"
        r = http.get("/scim/v2/Groups", headers=scim, params={"filter": 'displayName eq "Staff"'})
        assert r.json()["totalResults"] == 1
        stale = http.put(
            f"/scim/v2/Groups/{g['id']}",
            headers={**scim, "If-Match": 'W/"1"'},
            json={"schemas": [GROUP], "displayName": "X"},
        )
        assert stale.status_code == 412
        nameless = http.put(f"/scim/v2/Groups/{g['id']}", headers=scim, json={"schemas": [GROUP]})
        assert nameless.status_code == 400

        # ---- HR lifecycle: the SCIM user reads back the state the event caused
        ev = {"user_id": second, "event": "LEAVER"}
        naive = http.post(
            "/provisioning/lifecycle-events",
            headers=scim,
            json={**ev, "effective_at": "2026-01-01T00:00:00"},
        )
        assert naive.status_code == 422
        r = http.post("/provisioning/lifecycle-events", headers=scim, json=ev)
        assert r.status_code == 200, r.text
        off = http.get(f"/scim/v2/Users/{second}", headers=scim).json()
        assert off["active"] is False and off["meta"]["version"] == 'W/"2"'
        r = http.post(
            "/provisioning/lifecycle-events",
            headers=scim,
            json={"user_id": second, "event": "LEAVE_END"},
        )
        assert r.status_code == 200, r.text
        on = http.get(f"/scim/v2/Users/{second}", headers=scim).json()
        assert on["active"] is True and on["meta"]["version"] == 'W/"3"'

        # ---- delete honours If-Match; a suspended source is refused and shows as such
        stale_del = http.delete(f"/scim/v2/Users/{second}", headers={**scim, "If-Match": 'W/"1"'})
        assert stale_del.status_code == 412
        assert http.delete(f"/scim/v2/Users/{second}", headers=scim).status_code == 204
        r = http.post(f"/provisioning/sources/{src['source_id']}:suspend", headers=adm)
        assert r.status_code == 200 and r.json() == {**view, "status": "SUSPENDED"}
        listed = http.get("/provisioning/sources", headers=adm).json()["items"]
        assert listed[0]["status"] == "SUSPENDED"
        assert http.get("/scim/v2/Users", headers=scim).status_code == 403
        assert http.post(f"/provisioning/sources/{uid()}:suspend", headers=adm).status_code == 404

        # ---- the platform creates the connector's OAuth client from its PUBLIC keys
        key = LocalSigner("scim-connector")
        r = http.post(
            "/provisioning/sources",
            headers=adm,
            json={"display_name": "Okta", "jwks": {"keys": [key.public_jwk()]}},
        )
        assert r.status_code == 201, r.text
        made = r.json()
        assert made["client_created"] is True and made["client_id"].startswith("okta-")
        assert made["token_endpoint"] == f"{ISS}/oauth2/token"
        client_row = asyncio.run(oauth_client(made["client_id"]))
        assert client_row.client_type == "CONFIDENTIAL" and client_row.status == "ACTIVE"
        assert list(client_row.grant_types) == ["client_credentials"]
        assert list(client_row.scopes) == ["scim:provision"]
        assert client_row.token_auth_method == "private_key_jwt"
        assert client_row.service_principal_id is not None
        assert client_row.jwks["keys"][0]["kid"] == "scim-connector"
        assert not {"d", "p", "q", "k"} & client_row.jwks["keys"][0].keys()
        # the new client really authenticates (private_key_jwt) and its token drives SCIM
        now = int(time.time())
        assertion = sign_jws(
            key,
            {
                "iss": made["client_id"],
                "sub": made["client_id"],
                "aud": ISS,
                "jti": uid(),
                "iat": now,
                "exp": now + 60,
            },
            typ="JWT",
        )
        r = http.post(
            "/oauth2/token",
            data={
                "grant_type": "client_credentials",
                "scope": "scim:provision",
                "client_id": made["client_id"],
                "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
                "client_assertion": assertion,
            },
        )
        assert r.status_code == 200, r.text
        _, at = verify_jws(r.json()["access_token"], keys=ring.jwks()["keys"], typ="at+jwt")
        assert at["sub"] == client_row.service_principal_id and at["scope"] == "scim:provision"
        auto = {"Authorization": f"Bearer {r.json()['access_token']}"}
        r = http.post("/scim/v2/Users", headers=auto, json={"schemas": [USER], "userName": "okta1"})
        assert r.status_code == 201, r.text
        assert http.get("/scim/v2/Users", headers=auto).json()["totalResults"] == 1

        # exactly one of jwks / client_id; private keys and bad key sets are refused (422)
        pub = key.public_jwk()
        for bad in (
            {"display_name": "X"},
            {"display_name": "X", "client_id": client, "jwks": {"keys": [pub]}},
            {"display_name": "X", "jwks": {"keys": [{**pub, "d": "secret"}]}},
            {"display_name": "X", "jwks": {"keys": []}},
            {"display_name": "X", "jwks": {"keys": [{k: v for k, v in pub.items() if k != "kid"}]}},
        ):
            r = http.post("/provisioning/sources", headers=adm, json=bad)
            assert r.status_code == 422, (bad, r.text)
        assert (
            "secret"
            not in http.post(
                "/provisioning/sources",
                headers=adm,
                json={"display_name": "X", "jwks": {"keys": [{**pub, "d": "secret"}]}},
            ).text
        )

        # an existing client must exist here, be ACTIVE and hold client_credentials + scim:provision:
        # unknown, foreign, retired and wrongly-granted all answer the same 422
        foreign_tenant = uid()
        asyncio.run(seed_client(foreign_tenant, "foreign-client"))
        asyncio.run(seed_client(tenant, "no-scope-client", scope="openid"))
        asyncio.run(seed_client(tenant, "retired-client"))
        asyncio.run(retire("retired-client"))
        answers = set()
        for cid in ("unknown-client", "foreign-client", "no-scope-client", "retired-client"):
            r = http.post(
                "/provisioning/sources", headers=adm, json={"display_name": "X", "client_id": cid}
            )
            assert r.status_code == 422, (cid, r.text)
            answers.add(r.json()["error"]["message"])
            assert r.json()["error"]["code"] == "PROVISIONING_CLIENT_INVALID"
        assert len(answers) == 1

        # ---- resume: SUSPENDED -> ACTIVE (idempotent), 404 unknown, 409 once its client is retired
        sid = src["source_id"]
        r = http.post(f"/provisioning/sources/{sid}:resume", headers=adm)
        assert r.status_code == 200 and r.json() == {**view, "status": "ACTIVE"}, r.text
        assert http.get("/scim/v2/Users", headers=scim).status_code == 200
        r = http.post(f"/provisioning/sources/{sid}:resume", headers=adm, json={})
        assert r.status_code == 200 and r.json()["status"] == "ACTIVE"  # idempotent
        assert http.post(f"/provisioning/sources/{uid()}:resume", headers=adm).status_code == 404
        assert http.post(f"/provisioning/sources/{sid}:suspend", headers=adm).status_code == 200
        asyncio.run(retire(client))
        r = http.post(f"/provisioning/sources/{sid}:resume", headers=adm)
        assert r.status_code == 409 and r.json()["error"]["code"] == "PROVISIONING_CLIENT_RETIRED"
        assert (
            http.get("/provisioning/sources", headers=adm).json()["items"][-1]["status"]
            == "SUSPENDED"
        )
        assert (
            http.post(
                f"/provisioning/sources/{sid}:resume", headers=adm, json={"unknown": 1}
            ).status_code
            == 422
        )
