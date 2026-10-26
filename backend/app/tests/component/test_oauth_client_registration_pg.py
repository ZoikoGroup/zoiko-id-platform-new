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
