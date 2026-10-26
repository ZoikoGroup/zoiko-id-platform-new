"""Confidential clients, mTLS, PAR/FAPI 2.0, client credentials, token exchange (AUTH-W3).

ZID-AUTH-001 §6.1-6.2, §7, §13; RFC 7523, 8705, 9126, 8693, 7662. Real client assertions, a real
X.509 client certificate forwarded the way the TLS terminator does, real DPoP proofs.
"""

from __future__ import annotations

import asyncio
import secrets
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, quote, urlsplit

from conftest import ctx, engine
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from fastapi.testclient import TestClient
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
    set_status,
)
from zid_platform.modules.session.domain.oauth import OAuthClient, pkce_challenge
from zid_platform.modules.session.domain.session import Session, SessionStatus
from zid_platform.modules.session.domain.tokens import access_token_hash
from zid_platform.modules.session.infrastructure.oauth_store import create_client
from zid_platform.modules.session.infrastructure.session_store import (
    create_session,
    issue_handle,
)
from zid_platform.modules.session.infrastructure.session_store import (
    set_status as set_session_status,
)
from zid_platform.settings import Settings

ISS, AUD, API2 = "https://id.zoiko.test", "https://api.zoiko.test", "https://api2.zoiko.test"
CB = "https://app.example/cb"
COOKIE = "__Host-zid_session"
FORM = {"Content-Type": "application/x-www-form-urlencoded"}
DN = "CN=mtls-client,O=Zoiko Test"
CERT_HEADER = "x-client-cert"


def uid() -> str:
    return str(uuid.uuid4())


def make_cert(cn: str) -> str:
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name(
        [
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Zoiko Test"),
            x509.NameAttribute(NameOID.COMMON_NAME, cn),
        ]
    )
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    return quote(cert.public_bytes(serialization.Encoding.PEM).decode())


def base_client(cid: str, tenant: str, **kw: object) -> OAuthClient:
    args: dict[str, object] = {
        "client_id": cid,
        "tenant_id": tenant,
        "client_type": "CONFIDENTIAL",
        "profile": "STANDARD",
        "redirect_uris": (CB,),
        "grant_types": frozenset({"authorization_code"}),
        "scopes": frozenset({"openid", "grants:read"}),
        "audiences": (AUD, API2),
        "token_auth_method": "private_key_jwt",
        "dpop_bound": False,
        "status": "ACTIVE",
        "first_party": True,
    }
    return OAuthClient(**{**args, **kw})  # type: ignore[arg-type]


def test_confidential_clients_mtls_par_fapi_exchange_and_introspection(rt_url: str) -> None:
    """MACH-23: an exchanged token never has a wider audience, scope, life or actor authority than its subject token."""
    tenant, alice, svc, svc2 = uid(), uid(), uid(), uid()
    conf, fapi, mtls, spa = (f"{n}-{uid()}" for n in ("conf", "fapi", "mtls", "spa"))
    conf_key, fapi_key, rogue = LocalSigner("conf-1"), LocalSigner("fapi-1"), LocalSigner("x")
    sid = uid()

    async def seed() -> str:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            await create_principal(
                c, Principal(alice, tenant, PrincipalType.HUMAN, PrincipalStatus.ACTIVE, "uk-1")
            )
            for s in (svc, svc2):
                await create_principal(
                    c,
                    Principal(s, tenant, PrincipalType.APPLICATION, PrincipalStatus.ACTIVE, "uk-1"),
                )
            now = datetime.now(UTC)
            await create_session(
                c,
                Session(
                    sid,
                    tenant,
                    alice,
                    now - timedelta(minutes=1),
                    "native:webauthn",
                    2,
                    0,
                    1,
                    SessionStatus.ACTIVE,
                    now + timedelta(hours=8),
                ),
            )
            all_grants = frozenset(
                {
                    "authorization_code",
                    "refresh_token",
                    "client_credentials",
                    "urn:ietf:params:oauth:grant-type:token-exchange",
                }
            )
            await create_client(
                c,
                base_client(
                    conf,
                    tenant,
                    grant_types=all_grants,
                    scopes=frozenset({"openid", "grants:read", "grants:write", "zid:introspect"}),
                    jwks={"keys": [conf_key.public_jwk()]},
                    service_principal_id=svc,
                ),
            )
            await create_client(
                c,
                base_client(
                    fapi,
                    tenant,
                    profile="FAPI2",
                    dpop_bound=True,
                    require_par=True,
                    jwks={"keys": [fapi_key.public_jwk()]},
                ),
            )
            await create_client(
                c,
                base_client(
                    mtls,
                    tenant,
                    token_auth_method="tls_client_auth",
                    tls_subject_dn=DN,
                    grant_types=frozenset({"client_credentials"}),
                    service_principal_id=svc2,
                ),
            )
            await create_client(
                c,
                base_client(
                    spa,
                    tenant,
                    client_type="PUBLIC",
                    token_auth_method="none",
                    grant_types=frozenset({"client_credentials"}),
                    service_principal_id=svc,
                ),
            )
            return await issue_handle(c, sid)

    handle = asyncio.run(seed())
    ring = KeyRing(LocalSigner("issuer-1"))
    app = create_app(
        Settings(
            database_url=rt_url,
            token_issuer=ISS,
            token_audience=AUD,
            public_base_url=AUD,
            environment="test",
            mtls_client_cert_header=CERT_HEADER,
        ),
        keyring=ring,
    )
    dpop_key = LocalSigner("dpop")
    good_cert, other_cert = make_cert("mtls-client"), make_cert("someone-else")

    def assertion(cid: str, key: LocalSigner, **kw: object) -> dict[str, str]:
        now = int(time.time())
        claims = {"iss": cid, "sub": cid, "aud": ISS, "jti": uid(), "iat": now, "exp": now + 60}
        return {
            "client_id": cid,
            "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
            "client_assertion": sign_jws(key, {**claims, **kw}, typ="JWT"),
        }

    def proof(
        url: str = ISS + "/oauth2/token", method: str = "POST", tok: str | None = None
    ) -> str:
        body: dict[str, Any] = {"htm": method, "htu": url, "jti": uid(), "iat": int(time.time())}
        if tok:
            body["ath"] = access_token_hash(tok)
        return sign_jws(dpop_key, body, typ="dpop+jwt", extra_header={"jwk": dpop_key.public_jwk()})

    path = f"/v1/authority-grants/{uid()}"

    with TestClient(app, base_url=ISS, follow_redirects=False) as http:

        def post(endpoint: str, form: dict[str, str], **headers: str) -> Any:
            return http.post(f"/oauth2/{endpoint}", data=form, headers={**FORM, **headers})

        # RFC 7523 private_key_jwt + client_credentials: the subject is the service principal
        r = post(
            "token",
            {
                "grant_type": "client_credentials",
                "scope": "grants:read",
                **assertion(conf, conf_key),
            },
        )
        assert r.status_code == 200, r.text
        cc = r.json()
        assert cc["token_type"] == "Bearer" and "refresh_token" not in cc
        _, at = verify_jws(cc["access_token"], keys=ring.jwks()["keys"], typ="at+jwt")
        assert at["sub"] == svc and "sid" not in at and at["scope"] == "grants:read"
        assert (
            http.get(path, headers={"Authorization": f"Bearer {cc['access_token']}"}).status_code
            == 404
        )

        same = assertion(conf, conf_key)
        assert post("token", {"grant_type": "client_credentials", **same}).status_code == 200
        r = post("token", {"grant_type": "client_credentials", **same})
        assert r.status_code == 401 and r.json()["error"] == "invalid_client"  # assertion replay
        for bad in (
            assertion(conf, rogue),
            assertion(conf, conf_key, aud="https://elsewhere"),
            assertion(conf, conf_key, iss="someone"),
            assertion(conf, conf_key, exp=int(time.time()) - 600),
            assertion(conf, conf_key, exp=int(time.time()) + 3600),
            {"client_id": conf},
        ):
            r = post("token", {"grant_type": "client_credentials", **bad})
            assert r.status_code == 401 and r.json()["error"] == "invalid_client", bad
        # a public client never gets client credentials, even with a service principal
        r = post("token", {"grant_type": "client_credentials", "client_id": spa})
        assert r.json()["error"] == "unauthorized_client"
        r = post(
            "token",
            {"grant_type": "client_credentials", "client_id": spa, **assertion(spa, conf_key)},
        )
        assert r.status_code == 401

        # RFC 8705 tls_client_auth: certificate-bound tokens, no bearer fallback
        r = post(
            "token",
            {"grant_type": "client_credentials", "client_id": mtls},
            **{CERT_HEADER: good_cert},
        )
        assert r.status_code == 200, r.text
        bound = r.json()["access_token"]
        _, at = verify_jws(bound, keys=ring.jwks()["keys"], typ="at+jwt")
        assert set(at["cnf"]) == {"x5t#S256"} and at["sub"] == svc2
        auth = {"Authorization": f"Bearer {bound}"}
        assert http.get(path, headers={**auth, CERT_HEADER: good_cert}).status_code == 404
        r = http.get(path, headers=auth)
        assert r.status_code == 401 and r.json()["error"]["code"] == "MTLS_CERTIFICATE_REQUIRED"
        r = http.get(path, headers={**auth, CERT_HEADER: other_cert})
        assert r.json()["error"]["code"] == "MTLS_CERTIFICATE_MISMATCH"
        for headers in ({}, {CERT_HEADER: other_cert}, {CERT_HEADER: "garbage"}):
            r = post("token", {"grant_type": "client_credentials", "client_id": mtls}, **headers)
            assert r.status_code == 401 and r.json()["error"] == "invalid_client"

        # FAPI 2.0: PAR is required, the code is DPoP-bound through the PAR proof
        verifier = secrets.token_urlsafe(48)
        req = {
            "response_type": "code",
            "redirect_uri": CB,
            "state": "st",
            "scope": "openid grants:read",
            "nonce": "nn",
            "code_challenge": pkce_challenge(verifier),
            "code_challenge_method": "S256",
        }
        cookie = {"Cookie": f"{COOKIE}={handle}"}
        r = http.get("/oauth2/authorize", params={"client_id": fapi, **req}, headers=cookie)
        q = parse_qs(urlsplit(r.headers["location"]).query)
        assert r.status_code == 303 and q["error"] == ["invalid_request"]  # not pushed
        r = post("par", {**req, **assertion(fapi, fapi_key)}, DPoP=proof(ISS + "/oauth2/par"))
        assert r.status_code == 201, r.text
        assert r.json()["expires_in"] <= 60 and r.headers["cache-control"] == "no-store"
        uri = r.json()["request_uri"]
        r = post("par", {**req, "redirect_uri": "https://evil/cb", **assertion(fapi, fapi_key)})
        assert r.status_code == 400 and r.json()["error"] == "invalid_request"
        assert post("par", {**req, "client_id": fapi}).status_code == 401  # unauthenticated PAR
        r = http.get(
            "/oauth2/authorize",
            params={"client_id": fapi, "request_uri": uri, "redirect_uri": "https://evil/cb"},
            headers=cookie,
        )
        loc = urlsplit(r.headers["location"])
        assert (
            r.status_code == 303 and f"{loc.scheme}://{loc.netloc}{loc.path}" == CB
        )  # pushed wins
        code = parse_qs(loc.query)["code"][0]
        r = http.get(
            "/oauth2/authorize", params={"client_id": fapi, "request_uri": uri}, headers=cookie
        )
        assert (
            r.status_code == 400 and r.json()["error"] == "invalid_request_uri"
        )  # one transaction
        redeem = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": CB,
            "code_verifier": verifier,
        }
        r = post("token", {**redeem, **assertion(fapi, fapi_key)})
        assert r.json()["error"] == "invalid_dpop_proof"  # FAPI 2.0 tokens are sender-constrained
        r = post("token", {**redeem, **assertion(fapi, fapi_key)}, DPoP=proof())
        assert r.status_code == 200, r.text
        assert r.json()["token_type"] == "DPoP"

        # token exchange (RFC 8693): delegation with an actor chain; only narrows
        verifier = secrets.token_urlsafe(48)
        r = http.get(
            "/oauth2/authorize",
            params={
                "client_id": conf,
                **req,
                "scope": "openid grants:read grants:write",
                "code_challenge": pkce_challenge(verifier),
            },
            headers=cookie,
        )
        code = parse_qs(urlsplit(r.headers["location"]).query)["code"][0]
        r = post(
            "token",
            {**redeem, "code": code, "code_verifier": verifier, **assertion(conf, conf_key)},
        )
        assert r.status_code == 200, r.text
        user_tok, user_refresh = r.json()["access_token"], r.json()["refresh_token"]
        xchg = {
            "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
            "subject_token": user_tok,
            "subject_token_type": "urn:ietf:params:oauth:token-type:access_token",
        }
        r = post(
            "token", {**xchg, "resource": API2, "scope": "grants:read", **assertion(conf, conf_key)}
        )
        assert r.status_code == 200, r.text
        ex = r.json()
        assert ex["issued_token_type"] == "urn:ietf:params:oauth:token-type:access_token"
        _, et = verify_jws(ex["access_token"], keys=ring.jwks()["keys"], typ="at+jwt")
        _, ut = verify_jws(user_tok, keys=ring.jwks()["keys"], typ="at+jwt")
        assert et["sub"] == alice and et["aud"] == API2 and et["sid"] == sid
        assert et["act"] == {"sub": svc, "client_id": conf} and et["exp"] <= ut["exp"]
        assert et["scope"] == "grants:read"
        r = post("token", {**xchg, "resource": API2, "scope": "admin", **assertion(conf, conf_key)})
        assert r.json()["error"] == "invalid_scope"
        r = post("token", {**xchg, "resource": "https://unregistered", **assertion(conf, conf_key)})
        assert r.json()["error"] == "invalid_target"
        r = post("token", {**xchg, "subject_token": "x.y.z", **assertion(conf, conf_key)})
        assert r.json()["error"] == "invalid_grant"
        r = post("token", {**xchg, "client_id": fapi, **assertion(fapi, fapi_key)}, DPoP=proof())
        assert r.json()["error"] == "unauthorized_client"  # grant not registered

        # introspection (RFC 7662) for an authorized confidential client
        def introspect(tok: str, cid: str = conf, key: LocalSigner = conf_key) -> Any:
            return post("introspect", {"token": tok, **assertion(cid, key)})

        r = introspect(ex["access_token"])
        assert r.status_code == 200 and r.json()["active"] is True
        assert r.json()["sub"] == alice and r.json()["act"] == {"sub": svc}
        assert introspect(user_refresh).json()["token_type"] == "refresh_token"
        assert introspect("nonsense").json() == {"active": False}
        assert introspect(user_tok, fapi, fapi_key).status_code == 403  # not allowed to introspect

        # UserInfo needs the openid scope
        assert http.get(
            "/oauth2/userinfo", headers={"Authorization": f"Bearer {user_tok}"}
        ).json() == {
            "sub": alice,
            "tenant_id": tenant,
        }
        r = http.get("/oauth2/userinfo", headers={"Authorization": f"Bearer {cc['access_token']}"})
        assert r.status_code == 403

        # revoking the user's session ends the exchanged token too (it keeps the session id)
        async def revoke_session() -> None:
            async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
                await set_session_status(c, sid, SessionStatus.REVOKED, reason="test")
                await set_status(c, svc2, PrincipalStatus.SUSPENDED)

        asyncio.run(revoke_session())
        assert introspect(ex["access_token"]).json() == {"active": False}
        r = post("token", {**xchg, "resource": API2, **assertion(conf, conf_key)})
        assert r.json()["error"] == "invalid_grant"
        # a suspended service principal's token stops working at once
        r = http.get(path, headers={**auth, CERT_HEADER: good_cert})
        assert r.status_code == 401 and r.json()["error"]["code"] == "PRINCIPAL_NOT_ACTIVE"
        r = post(
            "token",
            {"grant_type": "client_credentials", "client_id": mtls},
            **{CERT_HEADER: good_cert},
        )
        assert r.json()["error"] == "unauthorized_client"

        meta = http.get("/.well-known/oauth-authorization-server").json()
        assert meta["pushed_authorization_request_endpoint"] == ISS + "/oauth2/par"
        assert "tls_client_auth" in meta["token_endpoint_auth_methods_supported"]
        assert "client_secret_basic" not in meta["token_endpoint_auth_methods_supported"]

    # billing (CR-025): only the three token grants that succeeded (two private_key_jwt, one mTLS) are M2M events; the replayed assertion, the
    # bad assertions and the public client's refusal bill nothing; the key is the token's own id
    async def billed() -> list[str]:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            rows = (
                await c.execute(
                    text(
                        "SELECT deduplication_key FROM commercial.billable_event WHERE meter_id = 'M2M_GRANT'"
                    )
                )
            ).all()
        return [r[0] for r in rows]

    keys = asyncio.run(billed())
    assert len(keys) == 3 and len(set(keys)) == 3 and all(k.startswith("m2m:") for k in keys)
