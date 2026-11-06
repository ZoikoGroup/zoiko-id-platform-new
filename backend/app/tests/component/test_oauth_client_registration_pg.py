"""A company registers the OAuth clients of its own applications (ZID-AUTH-001 §6-7).

No secret exists: a public client uses PKCE, a confidential client registers only the public half of its key pair. A
pasted private key, an unsafe redirect and a platform-administrative scope are all refused.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from onboarding_fixture import AUD, ISS, onboard
from zid_platform.main import create_app
from zid_platform.settings import Settings

PUBLIC_JWK = {"kty": "EC", "crv": "P-256", "x": "f83OJ3D2xF1Bg8vub9tLe1gHMzV76e8Tus9uPHvRVEU", "y": "x_FEzRu9m36HLN_tue659LNpXW6pCyStikYjKIWI5a0", "kid": "k1"}  # fmt: skip


def test_registering_listing_and_retiring_a_client(rt_url: str) -> None:
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
    h = o.bearer(o.owner)
    with TestClient(app) as api:
        r = api.post(
            "/v1/integration/oauth-clients",
            json={
                "name": "Acme Web",
                "client_type": "PUBLIC",
                "redirect_uris": ["https://acme.example/cb"],
            },
            headers=h,
        )
        assert r.status_code == 201, r.text
        web = r.json()
        assert web["client_id"].startswith("acme-web-") and web["token_auth_method"] == "none"
        assert web["scopes"] == ["openid"] and "secret" not in r.text

        bad = {
            "name": "Acme Server",
            "client_type": "CONFIDENTIAL",
            "redirect_uris": ["https://acme.example/cb"],
        }
        for body, code in (
            ({**bad, "jwks": {"keys": [{**PUBLIC_JWK, "d": "x"}]}}, "JWKS_PRIVATE_KEY"),  # a private key
            ({**bad, "jwks": None}, "JWKS_INVALID"),  # a server app must bring a key
            ({**bad, "jwks": {"keys": [PUBLIC_JWK]}, "scopes": ["tenant:admin"]}, "SCOPE_NOT_AVAILABLE"),
            ({**bad, "jwks": {"keys": [PUBLIC_JWK]}, "redirect_uris": ["http://acme.example/cb"]}, "REDIRECT_URI_INVALID"),
            ({"name": "Phone", "client_type": "PUBLIC", "redirect_uris": ["https://a.example/cb"], "jwks": {"keys": [PUBLIC_JWK]}}, "JWKS_NOT_ALLOWED"),
        ):  # fmt: skip
            r = api.post("/v1/integration/oauth-clients", json=body, headers=h)
            assert r.status_code == 422 and r.json()["error"]["code"] == code, (code, r.text)

        r = api.post(
            "/v1/integration/oauth-clients",
            json={
                **bad,
                "jwks": {"keys": [PUBLIC_JWK]},
                "scopes": ["openid", "verification:read"],
                "redirect_uris": ["https://acme.example/cb", "http://127.0.0.1:3000/cb"],
            },
            headers=h,
        )
        assert r.status_code == 201, r.text
        server = r.json()
        assert (server["token_auth_method"], server["key_ids"]) == ("private_key_jwt", ["k1"])

        listed = {
            c["client_id"]: c
            for c in api.get("/v1/integration/oauth-clients", headers=h).json()["items"]
        }
        assert {web["client_id"], server["client_id"]} <= set(listed)
        assert all("jwks" not in c for c in listed.values())  # key ids, never key material

        r = api.post(f"/v1/integration/oauth-clients/{web['client_id']}:retire", headers=h)
        assert r.status_code == 200 and r.json()["status"] == "RETIRED", r.text
        after = {
            c["client_id"]: c
            for c in api.get("/v1/integration/oauth-clients", headers=h).json()["items"]
        }
        assert (
            after[web["client_id"]]["status"] == "RETIRED"
            and after[server["client_id"]]["status"] == "ACTIVE"
        )

        # the company's own console client is first-party and cannot be retired from here
        console = [c for c in after.values() if c["first_party"]]
        for c in console:
            r = api.post(f"/v1/integration/oauth-clients/{c['client_id']}:retire", headers=h)
            assert r.status_code == 404


def test_an_application_that_asks_for_decisions_gets_a_machine_client(rt_url: str) -> None:
    """The decision caller must be able to get a machine token: `client_credentials` only, a signed key, no redirect, its own service identity, and
    no way to be used for people to sign in. A sign-in client still needs its redirect address."""
    import asyncio

    from conftest import ctx, engine
    from sqlalchemy import text
    from zid_db import tenant_transaction
    from zid_platform.modules.session.ports import usable_service_client

    o = onboard(rt_url)
    app = create_app(
        Settings(database_url=rt_url, token_issuer=ISS, token_audience=AUD, public_base_url=AUD, environment="test", cell_id="uk-1"),
        keyring=o.ring,
    )
    h = o.bearer(o.owner)
    body = {"name": "Payroll (access decisions)", "client_type": "CONFIDENTIAL", "purpose": "DECISIONS", "jwks": {"keys": [PUBLIC_JWK]}}
    with TestClient(app, base_url=ISS) as api:
        made = api.post("/v1/integration/oauth-clients", json=body, headers=h)
        assert made.status_code == 201, made.text
        client = made.json()
        assert client["client_type"] == "CONFIDENTIAL" and client["redirect_uris"] == [] and client["scopes"] == ["access:evaluate"]
        assert client["token_auth_method"] == "private_key_jwt" and client["key_ids"] == ["k1"]
        assert "secret" not in made.text.lower()
        # what the decision caller must not be: a client people sign in through, a public client, one without a key, one with a redirect
        for bad in (
            {**body, "client_type": "PUBLIC"},
            {k: v for k, v in body.items() if k != "jwks"},
            {**body, "redirect_uris": ["https://app.example/callback"]},
        ):
            assert api.post("/v1/integration/oauth-clients", json=bad, headers=h).status_code == 422
        # a sign-in client still needs a redirect address
        signin = {"name": "Portal", "client_type": "PUBLIC", "redirect_uris": []}
        assert api.post("/v1/integration/oauth-clients", json=signin, headers=h).status_code == 422

    async def stored() -> tuple[list[str], bool, str | None]:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(o.tenant)) as c:
            row = (await c.execute(text("SELECT grant_types, service_principal_id FROM authn.oauth_client WHERE client_id = :c"), {"c": client["client_id"]})).one()
            usable = await usable_service_client(c, client["client_id"], scope="access:evaluate")
            return list(row.grant_types), usable, str(row.service_principal_id) if row.service_principal_id else None

    grants, usable, principal = asyncio.run(stored())
    assert grants == ["client_credentials"] and usable is True and principal is not None
