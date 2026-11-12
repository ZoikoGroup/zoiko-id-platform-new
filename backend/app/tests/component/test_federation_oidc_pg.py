"""Enterprise OIDC federation end to end (ZID-AUTH-001 §9, §9.1, §20, §22, §24; AUTH-W4).

AUTH-08 (mix-up), AUTH-09 (link takeover), AUTH-15 (key rollover), AUTH-16 (IdP outage), explicit
linking, JIT provisioning and R3-protected administration. The upstream IdP is a fake on an httpx
MockTransport that signs real RS256 ID tokens and checks our private_key_jwt client assertion.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
from admin_fixture import make_admin
from conftest import ctx, engine
from fastapi.testclient import TestClient
from joserfc import jws
from joserfc.jwk import RSAKey
from sqlalchemy import text
from zid_crypto import KeyRing, LocalSigner, verify_jws
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
from zid_platform.modules.session.infrastructure.session_store import create_session, issue_handle
from zid_platform.modules.session.ports import issue_access_token
from zid_platform.settings import Settings

ISS, AUD, ORIGIN = "https://id.zoiko.test", "https://api.zoiko.test", "https://id.zoiko.test"
IDP = "https://login.acme.example"
COOKIE = "__Host-zid_session"


def uid() -> str:
    return str(uuid.uuid4())


class FakeIdP:
    """Records authorization requests; answers the token endpoint with a signed ID token."""

    def __init__(self, ring: KeyRing, client_id: str) -> None:
        self.key = RSAKey.generate_key(2048)
        self.ring, self.client_id = ring, client_id
        self.nonces: dict[str, str] = {}  # code -> nonce
        self.subject = "emp-1001"
        self.claims: dict[str, Any] = {}
        self.down = False
        self.signing_key: RSAKey | None = None

    def jwks(self, key: RSAKey | None = None) -> dict[str, Any]:
        k = key or self.key
        return {"keys": [{**k.as_dict(private=False), "kid": k.thumbprint(), "use": "sig"}]}

    def authorize(self, url: str) -> str:
        q = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
        assert (
            q["code_challenge_method"] == "S256"
            and q["redirect_uri"] == ISS + "/federation/callback"
        )
        code = uid()
        self.nonces[code] = q["nonce"]
        return f"/federation/callback?code={code}&state={q['state']}&iss={IDP}"

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("idp down")
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        # our client authentication: private_key_jwt signed by the Zoiko issuer key
        _, assertion = verify_jws(
            form["client_assertion"], keys=self.ring.jwks()["keys"], typ="JWT"
        )
        assert assertion["iss"] == self.client_id and assertion["aud"] == IDP + "/token"
        now = int(time.time())
        claims = {
            "iss": IDP,
            "aud": self.client_id,
            "sub": self.subject,
            "nonce": self.nonces[form["code"]],
            "iat": now,
            "exp": now + 300,
            "auth_time": now - 10,
            "acr": "phr",
            "amr": ["hwk", "mfa"],
            "email": "alice@acme.example",
            **self.claims,
        }
        key = self.signing_key or self.key
        token = jws.serialize_compact(
            {"alg": "RS256", "kid": key.thumbprint(), "typ": "JWT"},
            json.dumps(claims).encode(),
            key,
            algorithms=["RS256"],
        )
        return httpx.Response(
            200, json={"id_token": token, "access_token": "x", "token_type": "Bearer"}
        )


def test_oidc_federation_linking_rollover_and_outage(rt_url: str, owner_url: str) -> None:
    tenant, alice, bob, admin = uid(), uid(), uid(), uid()
    web = f"web-{uid()}"
    ring = KeyRing(LocalSigner("issuer-1"))
    idp = FakeIdP(ring, "zoiko-at-acme")

    async def seed() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            for p in (alice, bob, admin):
                await create_principal(
                    c, Principal(p, tenant, PrincipalType.HUMAN, PrincipalStatus.ACTIVE, "uk-1")
                )
            await c.execute(  # a real tenant record: people created by signing in become its members
                text(
                    "INSERT INTO platform.tenant (tenant_id, display_name, tenant_slug, status, deployment_mode, "
                    "home_cell_id, creator_tenant_id, creator_principal_id, created_at, state_changed_at) "
                    "VALUES (:t, 'Acme', :s, 'ACTIVE', 'O0', 'local-1', :t, :p, now(), now())"
                ),
                {"t": uuid.UUID(tenant), "s": f"acme-{tenant[:8]}", "p": uuid.UUID(admin)},
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
                c, tenant, admin, ["tenant.identity_provider.change"], ["federation_connection:*"]
            )

    async def session_for(p: str, *, fresh: bool = True) -> tuple[str, str]:
        sid, now = uid(), datetime.now(UTC)
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            principal, _ = await load_principal(c, p)
            auth_time = now - (timedelta(seconds=20) if fresh else timedelta(hours=1))
            await create_session(
                c,
                Session(
                    sid,
                    tenant,
                    p,
                    auth_time,
                    "native:webauthn",
                    2,
                    principal.security_revision,
                    1,
                    SessionStatus.ACTIVE,
                    now + timedelta(hours=8),
                    now,
                ),
            )
            return sid, await issue_handle(c, sid)

    asyncio.run(seed())
    app = create_app(
        Settings(
            database_url=rt_url,
            token_issuer=ISS,
            token_audience=AUD,
            public_base_url=AUD,
            environment="test",
            webauthn_rp_id="id.zoiko.test",
            webauthn_origins=(ORIGIN,),
        ),
        keyring=ring,
    )
    app.state.federation_settings = dataclasses.replace(
        app.state.federation_settings, transport=httpx.MockTransport(idp.handler)
    )

    with TestClient(app, base_url=ISS, follow_redirects=False) as http:
        admin_sid, _ = asyncio.run(session_for(admin))
        adm = {
            "Authorization": "Bearer "
            + issue_access_token(
                ring,
                issuer=ISS,
                audience=AUD,
                subject=admin,
                tenant_id=tenant,
                client_id=web,
                now=datetime.now(UTC),
                session_id=admin_sid,
            )
        }
        spec = {
            "protocol": "OIDC",
            "display_name": "Acme Entra",
            "issuer": IDP,
            "client_id": "zoiko-at-acme",
            "client_auth": "private_key_jwt",
            "authorization_endpoint": IDP + "/authorize",
            "token_endpoint": IDP + "/token",
            "jwks": idp.jwks(),
            "algorithms": ["RS256"],
            "acr_levels": {"phr": 2},
            "metadata_expires_at": (datetime.now(UTC) + timedelta(days=30)).isoformat(),
        }
        bob_sid, _ = asyncio.run(session_for(bob))
        nobody = {
            "Authorization": "Bearer "
            + issue_access_token(
                ring,
                issuer=ISS,
                audience=AUD,
                subject=bob,
                tenant_id=tenant,
                client_id=web,
                now=datetime.now(UTC),
                session_id=bob_sid,
            )
        }
        assert http.post("/federation/integrations", headers=nobody, json=spec).status_code == 403
        r = http.post("/federation/integrations", headers=adm, json=spec)
        assert r.status_code == 201, r.text
        conn_id = r.json()["connection_id"]
        r = http.post(
            "/federation/integrations", headers=adm, json={**spec, "token_endpoint": "http://plain"}
        )
        assert r.status_code == 409 or r.status_code == 422  # profile CHECK: https endpoints only

        # the sign-in page offers what the administrators connected (name and connection only), and nothing for an unknown client
        offered = http.get("/federation/sign-in-options", params={"client_id": web}).json()["items"]
        assert [x["id"] for x in offered if x["id"] == conn_id] == [conn_id]
        assert set(next(x for x in offered if x["id"] == conn_id)) == {
            "id",
            "label",
            "protocol",
        }  # no endpoint, key or secret
        assert http.get(
            "/federation/sign-in-options", params={"client_id": "no-such-client"}
        ).json() == {"items": []}

        def start(**params: str) -> str:
            r = http.get(
                f"/federation/connections/{conn_id}/login", params={"client_id": web, **params}
            )
            assert r.status_code == 303, r.text
            assert r.headers["location"].startswith(IDP + "/authorize?")
            return idp.authorize(r.headers["location"])

        def callback(path: str, **override: str) -> Any:
            if override:
                u = urlsplit(path)
                q = {k: v[0] for k, v in parse_qs(u.query).items()} | override
                return http.get(u.path, params=q)
            return http.get(path)

        # AUTH-09: an unlinked upstream identity - even one asserting alice's email - gets nothing
        r = callback(start())
        assert r.status_code == 401 and r.json()["error"]["code"] == "FEDERATION_ACCOUNT_NOT_LINKED"

        # explicit, authenticated linking needs a fresh strong session and an allowed origin
        _, alice_handle = asyncio.run(session_for(alice))
        link = {"client_id": web}
        r = http.post(
            f"/federation/connections/{conn_id}/link",
            json=link,
            headers={"Origin": "https://evil.example", "Cookie": f"{COOKIE}={alice_handle}"},
        )
        assert r.status_code == 403
        _, stale_handle = asyncio.run(session_for(alice, fresh=False))
        r = http.post(
            f"/federation/connections/{conn_id}/link",
            json=link,
            headers={"Origin": ORIGIN, "Cookie": f"{COOKIE}={stale_handle}"},
        )
        assert r.status_code == 403 and r.json()["error"]["code"] == "STEP_UP_REQUIRED"
        r = http.post(
            f"/federation/connections/{conn_id}/link",
            json=link,
            headers={"Origin": ORIGIN, "Cookie": f"{COOKIE}={alice_handle}"},
        )
        assert r.status_code == 200, r.text
        cb = idp.authorize(r.json()["authorization_url"])
        r = callback(cb)
        assert r.status_code == 200, r.text
        linked = r.json()
        assert linked["principal_id"] == alice and linked["linked"] is True and linked["aal"] == 2
        assert linked["authentication_context"] == f"oidc:{IDP}" and r.cookies.get(COOKIE)
        assert callback(cb).json()["error"]["code"] == "FEDERATION_STATE_INVALID"  # single use

        # the link now decides the principal on ordinary logins
        r = callback(start())
        assert (
            r.status_code == 200
            and r.json()["principal_id"] == alice
            and r.json()["linked"] is False
        )

        # another account cannot take over the same upstream identity
        _, bob_handle = asyncio.run(session_for(bob))
        r = http.post(
            f"/federation/connections/{conn_id}/link",
            json=link,
            headers={"Origin": ORIGIN, "Cookie": f"{COOKIE}={bob_handle}"},
        )
        r = callback(idp.authorize(r.json()["authorization_url"]))
        assert r.status_code == 409 and r.json()["error"]["code"] == "FEDERATION_LINK_CONFLICT"

        # AUTH-08: mix-up and token substitution are refused
        assert (
            callback(start(), iss="https://other-idp").json()["error"]["code"]
            == "FEDERATION_ISSUER_MISMATCH"
        )
        for claims, code in (
            ({"aud": "someone-else"}, "ID_TOKEN_AUDIENCE_MISMATCH"),
            ({"aud": ["zoiko-at-acme", "x"]}, "ID_TOKEN_AZP_MISMATCH"),
            ({"nonce": "replayed"}, "ID_TOKEN_NONCE_MISMATCH"),
            ({"iss": "https://other-idp"}, "ID_TOKEN_ISSUER_MISMATCH"),
            ({"exp": int(time.time()) - 600}, "ID_TOKEN_EXPIRED"),
        ):
            idp.claims = claims
            r = callback(start())
            assert r.status_code == 401 and r.json()["error"]["code"] == code, (claims, r.text)
        idp.claims = {}
        idp.signing_key = RSAKey.generate_key(2048)
        assert callback(start()).json()["error"]["code"] == "ID_TOKEN_SIGNATURE_INVALID"
        idp.signing_key = None

        # AUTH-16: upstream outage fails closed, no fallback
        idp.down = True
        r = callback(start())
        assert r.status_code == 502 and r.json()["error"]["code"] == "FEDERATION_UNAVAILABLE"
        idp.down = False

        # open redirect is impossible; a pending authorize request is continued
        r = http.get(
            f"/federation/connections/{conn_id}/login",
            params={"client_id": web, "return_to": "https://evil.example/x"},
        )
        assert r.status_code == 400
        r = callback(start(return_to="/oauth2/authorize?client_id=" + web))
        assert (
            r.status_code == 303 and r.headers["location"] == "/oauth2/authorize?client_id=" + web
        )

        # AUTH-15: key rollover - a login in flight dies with the old revision, the retired key fails
        in_flight = start()
        old_key, idp.key = idp.key, RSAKey.generate_key(2048)
        r = http.post(
            f"/federation/integrations/{conn_id}:rotate",
            headers=adm,
            json={
                "jwks": idp.jwks(),
                "metadata_expires_at": (datetime.now(UTC) + timedelta(days=30)).isoformat(),
            },
        )
        assert r.status_code == 200 and r.json()["config_revision"] == 2
        assert callback(in_flight).json()["error"]["code"] == "FEDERATION_CONFIG_CHANGED"
        idp.signing_key = old_key
        assert callback(start()).json()["error"]["code"] == "ID_TOKEN_SIGNATURE_INVALID"
        idp.signing_key = None
        assert callback(start()).status_code == 200

        # metadata past its validity authenticates nobody
        async def expire() -> None:
            async with engine(owner_url) as eng, eng.begin() as c:
                await c.execute(
                    text(
                        "UPDATE authn.federation_connection SET metadata_expires_at = now() "
                        "- interval '1 second' WHERE connection_id = :c"
                    ),
                    {"c": uuid.UUID(conn_id)},
                )

        asyncio.run(expire())
        r = http.get(f"/federation/connections/{conn_id}/login", params={"client_id": web})
        assert r.status_code == 401 and r.json()["error"]["code"] == "FEDERATION_METADATA_EXPIRED"

        # quarantine
        r = http.post(
            f"/federation/integrations/{conn_id}:rotate",
            headers=adm,
            json={"metadata_expires_at": (datetime.now(UTC) + timedelta(days=30)).isoformat()},
        )
        assert r.status_code == 200
        assert (
            http.post(f"/federation/integrations/{conn_id}:disable", headers=adm).json()["status"]
            == "QUARANTINED"
        )
        r = http.get(f"/federation/connections/{conn_id}/login", params={"client_id": web})
        assert r.json()["error"]["code"] == "FEDERATION_CONNECTION_QUARANTINED"
        assert conn_id not in [
            x["id"]
            for x in http.get("/federation/sign-in-options", params={"client_id": web}).json()[
                "items"
            ]
        ]  # a quarantined connection is no longer offered

        # JIT provisioning on a JIT connection creates a fresh principal - never attaches to alice
        jit_idp = "https://login.partner.example"
        r = http.post(
            "/federation/integrations",
            headers=adm,
            json={
                **spec,
                "issuer": jit_idp,
                "display_name": "Partner",
                "link_policy": "JIT",
                "jwks": idp.jwks(),  # the IdP's current keys (the spec above predates the rollover)
                "authorization_endpoint": IDP + "/authorize",
                "token_endpoint": IDP + "/token",
            },
        )
        jit_id = r.json()["connection_id"]
        idp.claims = {"iss": jit_idp}
        r = http.get(f"/federation/connections/{jit_id}/login", params={"client_id": web})
        r = callback(idp.authorize(r.headers["location"]).replace(f"iss={IDP}", f"iss={jit_idp}"))
        assert r.status_code == 200, r.text
        assert r.json()["linked"] is True and r.json()["principal_id"] not in (alice, bob, admin)
        jit_principal = r.json()["principal_id"]

        async def roles_held() -> list[tuple[str, str]]:
            async with engine(owner_url) as eng, eng.begin() as c:
                rows = await c.execute(
                    text(
                        "SELECT role, status FROM platform.tenant_role_assignment WHERE principal_id = :p"
                    ),
                    {"p": uuid.UUID(jit_principal)},
                )
                return [(str(a), str(b)) for a, b in rows.all()]

        # a person created by signing in is a plain MEMBER and never more: ADMIN only comes from an owner's invitation
        assert asyncio.run(roles_held()) == [("MEMBER", "ACTIVE")]
        listed = http.get("/federation/integrations", headers=adm).json()["items"]
        assert {i["issuer"] for i in listed} == {IDP, jit_idp}

        # A person the company's own directory sync provisioned attaches to THAT account at first sign-in (matched on the provider's id, never on
        # e-mail), even on a connection that accepts no strangers; someone the directory never provisioned is still refused.
        dir_idp, prov_principal, prov_source = "https://login.directory.example", uid(), uid()

        async def provision() -> None:
            async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
                await create_principal(
                    c,
                    Principal(
                        prov_principal, tenant, PrincipalType.HUMAN, PrincipalStatus.ACTIVE, "uk-1"
                    ),
                )
                t = uuid.UUID(tenant)
                await c.execute(
                    text(
                        "INSERT INTO platform.provisioning_source (source_id, tenant_id, client_id, display_name, status) VALUES (:s, :t, :c, 'HR', 'ACTIVE')"
                    ),
                    {"s": uuid.UUID(prov_source), "t": t, "c": f"hr-{prov_source[:8]}"},
                )
                await c.execute(
                    text(
                        "INSERT INTO platform.scim_resource (resource_id, tenant_id, source_id, resource_type, external_id, principal_id, user_name, active, "
                        "attributes, version, created_at, updated_at) VALUES (:r, :t, :s, 'User', 'auth0|prov-1', :p, 'prov1@acme.example', true, '{}', 1, now(), now())"
                    ),
                    {
                        "r": uuid.uuid4(),
                        "t": t,
                        "s": uuid.UUID(prov_source),
                        "p": uuid.UUID(prov_principal),
                    },
                )
                await c.execute(
                    text(
                        "INSERT INTO platform.directory_sync (sync_id, tenant_id, source_id, connector_id, provider, display_name, settings) "
                        "VALUES (:i, :t, :s, :k, 'AUTH0', 'Directory', CAST(:cfg AS jsonb))"
                    ),
                    {
                        "i": uuid.uuid4(),
                        "t": t,
                        "s": uuid.UUID(prov_source),
                        "k": uuid.uuid4(),
                        "cfg": '{"domain": "login.directory.example"}',
                    },
                )

        asyncio.run(provision())
        r = http.post(
            "/federation/integrations",
            headers=adm,
            json={
                **spec,
                "issuer": dir_idp,
                "display_name": "Directory",
                "link_policy": "EXISTING_ONLY",
                "jwks": idp.jwks(),
                "authorization_endpoint": IDP + "/authorize",
                "token_endpoint": IDP + "/token",
            },
        )
        dir_id = r.json()["connection_id"]

        def directory_login(subject: str) -> Any:
            idp.subject, idp.claims = subject, {"iss": dir_idp}
            r = http.get(f"/federation/connections/{dir_id}/login", params={"client_id": web})
            return callback(
                idp.authorize(r.headers["location"]).replace(f"iss={IDP}", f"iss={dir_idp}")
            )

        first = directory_login("auth0|prov-1")
        assert first.status_code == 200, first.text
        assert first.json()["principal_id"] == prov_principal and first.json()["linked"] is True
        again = directory_login("auth0|prov-1")
        assert again.json()["principal_id"] == prov_principal and again.json()["linked"] is False
        assert directory_login("auth0|stranger").status_code != 200

    async def facts() -> dict[str, int]:
        async with engine(owner_url) as eng, eng.begin() as c:
            rows = (
                await c.execute(
                    text(
                        "SELECT event_type, count(*) FROM events.outbox_event WHERE tenant_id = :t AND "
                        "event_type LIKE 'com.zoiko.id.federation.%' GROUP BY 1"
                    ),
                    {"t": uuid.UUID(tenant)},
                )
            ).all()
        return dict(rows)

    f = asyncio.run(facts())
    assert (
        f["com.zoiko.id.federation.link.created.v1"] == 3
    )  # alice (explicit) + JIT principal + the provisioned person
    assert (
        f["com.zoiko.id.federation.metadata.changed.v1"] == 6
    )  # 3 created, 2 rotated, 1 quarantined
