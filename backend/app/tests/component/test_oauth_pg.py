"""OAuth authorization server end to end (ZID-AUTH-001 §6, §7.1, §8; AUTH-03/04/05/06/07/19).

Real clients, codes, refresh families and sessions in PostgreSQL with RLS; real DPoP proofs. The
tokens it issues are then used against a protected API, so issuer and resource server agree.
"""

from __future__ import annotations

import asyncio
import secrets
import time
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

from conftest import ctx, engine
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy import text
from zid_crypto import KeyRing, LocalSigner, jwk_thumbprint, sign_jws, verify_jws
from zid_db import tenant_transaction
from zid_platform.main import create_app
from zid_platform.modules.principal.domain.principal import (
    Principal,
    PrincipalStatus,
    PrincipalType,
)
from zid_platform.modules.principal.infrastructure.principal_store import create_principal
from zid_platform.modules.session.domain.oauth import OAuthClient, pkce_challenge
from zid_platform.modules.session.domain.session import Session, SessionStatus
from zid_platform.modules.session.domain.tokens import access_token_hash
from zid_platform.modules.session.infrastructure.oauth_store import create_client
from zid_platform.modules.session.infrastructure.session_store import create_session, issue_handle
from zid_platform.settings import Settings

ISS, AUD = "https://id.zoiko.test", "https://api.zoiko.test"
CB = "https://app.example/cb"
COOKIE = "__Host-zid_session"
FORM = {"Content-Type": "application/x-www-form-urlencoded"}


def uid() -> str:
    return str(uuid.uuid4())


def client(cid: str, tenant: str, **kw: object) -> OAuthClient:
    args: dict[str, object] = {
        "client_id": cid,
        "tenant_id": tenant,
        "client_type": "PUBLIC",
        "profile": "STANDARD",
        "redirect_uris": (CB, "http://127.0.0.1/cb"),
        "grant_types": frozenset({"authorization_code", "refresh_token"}),
        "scopes": frozenset({"openid", "grants:read", "grants:write"}),
        "audiences": (AUD,),
        "token_auth_method": "none",
        "dpop_bound": True,
        "status": "ACTIVE",
        "first_party": True,
    }
    return OAuthClient(**{**args, **kw})  # type: ignore[arg-type]


def test_authorization_code_pkce_dpop_refresh_rotation_and_revocation(
    rt_url: str, owner_url: str
) -> None:
    tenant, alice = uid(), uid()
    spa, third, noncey = f"spa-{uid()}", f"third-{uid()}", f"nonce-{uid()}"

    async def new_session() -> tuple[str, str]:
        sid = uid()
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            await create_session(
                c,
                Session(
                    sid,
                    tenant,
                    alice,
                    datetime.now(UTC) - timedelta(minutes=1),
                    "native:webauthn",
                    2,
                    0,
                    1,
                    SessionStatus.ACTIVE,
                    datetime.now(UTC) + timedelta(hours=8),
                ),
            )
            return sid, await issue_handle(c, sid)

    async def seed() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            await create_principal(
                c, Principal(alice, tenant, PrincipalType.HUMAN, PrincipalStatus.ACTIVE, "uk-1")
            )
            await create_client(c, client(spa, tenant))
            await create_client(c, client(third, tenant, first_party=False))
            await create_client(c, client(noncey, tenant, require_dpop_nonce=True))

    asyncio.run(seed())
    sid, handle = asyncio.run(new_session())
    ring = KeyRing(LocalSigner("issuer-1"))
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
    key = LocalSigner("client-key")
    jkt = jwk_thumbprint(key.public_jwk())

    def proof(
        signer: LocalSigner = key,
        *,
        url: str = ISS + "/oauth2/token",
        method: str = "POST",
        tok: str | None = None,
        **kw: object,
    ) -> str:
        body: dict[str, object] = {"htm": method, "htu": url, "jti": uid(), "iat": int(time.time())}
        if tok:
            body["ath"] = access_token_hash(tok)
        return sign_jws(
            signer, {**body, **kw}, typ="dpop+jwt", extra_header={"jwk": signer.public_jwk()}
        )

    with TestClient(app, base_url=ISS, follow_redirects=False) as http:

        def authorize(cid: str = spa, cookie: str | None = None, **params: str) -> Response:
            q = {
                "client_id": cid,
                "redirect_uri": CB,
                "response_type": "code",
                "state": "s-1",
                "scope": "openid grants:read",
                "nonce": "n-1",
                "code_challenge_method": "S256",
                **params,
            }
            headers = {"Cookie": f"{COOKIE}={cookie or handle}"} if cookie != "" else {}
            return http.get("/oauth2/authorize", params=q, headers=headers)

        def redirect_params(r: Response) -> dict[str, str]:
            assert r.status_code == 303, r.text
            loc = urlsplit(r.headers["location"])
            assert f"{loc.scheme}://{loc.netloc}{loc.path}" in (CB, "http://127.0.0.1:53111/cb")
            return {k: v[0] for k, v in parse_qs(loc.query).items()}

        def new_code(cid: str = spa, cookie: str | None = None) -> tuple[str, str]:
            verifier = secrets.token_urlsafe(48)
            q = redirect_params(authorize(cid, cookie, code_challenge=pkce_challenge(verifier)))
            assert q["state"] == "s-1" and q["iss"] == ISS, q
            return q["code"], verifier

        def token(form: dict[str, str], dpop: str | None = None, **headers: str) -> Response:
            h = {**FORM, **headers}
            if dpop:
                h["DPoP"] = dpop
            return http.post("/oauth2/token", data=form, headers=h)

        def redeem(code: str, verifier: str, cid: str = spa, dpop: str | None = "auto") -> Response:
            form = {
                "grant_type": "authorization_code",
                "client_id": cid,
                "code": code,
                "redirect_uri": CB,
                "code_verifier": verifier,
            }
            return token(form, proof() if dpop == "auto" else dpop)

        def refresh(rt: str, signer: LocalSigner = key, **extra: str) -> Response:
            form = {"grant_type": "refresh_token", "client_id": spa, "refresh_token": rt, **extra}
            return token(form, proof(signer))

        # metadata and keys (RFC 8414, OIDC Discovery)
        meta = http.get("/.well-known/openid-configuration").json()
        assert meta["issuer"] == ISS and meta["token_endpoint"] == ISS + "/oauth2/token"
        assert meta["code_challenge_methods_supported"] == ["S256"]
        assert "implicit" not in meta["grant_types_supported"]
        assert meta["response_types_supported"] == ["code"]
        assert http.get("/.well-known/oauth-authorization-server").json()["issuer"] == ISS
        jwks = http.get("/oauth2/jwks").json()
        assert jwks == ring.jwks()

        # AUTH-05: an unknown client or unregistered redirect is shown an error, never redirected
        r = authorize(cid="nobody", code_challenge=pkce_challenge("x" * 43))
        assert r.status_code == 400 and r.json()["error"] == "invalid_client"
        for bad in (
            "https://app.example/cb/../evil",
            "https://app.example/cb?x=1",
            "https://evil/cb",
        ):
            r = authorize(redirect_uri=bad, code_challenge=pkce_challenge("x" * 43))
            assert r.status_code == 400 and r.json()["error"] == "invalid_request", bad
        # loopback redirects may change only the port (RFC 8252 §7.3)
        q = redirect_params(
            authorize(
                redirect_uri="http://127.0.0.1:53111/cb", code_challenge=pkce_challenge("y" * 43)
            )
        )
        assert "code" in q
        # duplicate parameters are refused
        r = http.get(
            "/oauth2/authorize?client_id=" + spa + "&client_id=" + spa,
            headers={"Cookie": f"{COOKIE}={handle}"},
        )
        assert r.status_code == 400 and r.json()["error"] == "invalid_request"

        # PKCE is mandatory and S256 only; no session or a third-party client never gets a code
        assert redirect_params(authorize())["error"] == "invalid_request"
        q = redirect_params(authorize(code_challenge="a" * 43, code_challenge_method="plain"))
        assert q["error"] == "invalid_request" and q["state"] == "s-1" and q["iss"] == ISS
        assert redirect_params(authorize(code_challenge="a" * 43, cookie=""))["error"] == (
            "login_required"
        )
        assert redirect_params(authorize(code_challenge="a" * 43, cookie="forged"))["error"] == (
            "login_required"
        )
        assert redirect_params(authorize(third, code_challenge="a" * 43))["error"] == (
            "consent_required"
        )
        assert (
            redirect_params(authorize(code_challenge="a" * 43, scope="openid admin"))["error"]
            == "invalid_scope"
        )
        assert redirect_params(authorize(code_challenge="a" * 43, nonce=""))["error"] == (
            "invalid_request"
        )  # an ID token needs a nonce
        assert redirect_params(authorize(code_challenge="a" * 43, prompt="login"))["error"] == (
            "login_required"
        )

        # AUTH-04: a code without the right verifier is burned, not redeemable later
        code, verifier = new_code()
        r = redeem(code, "w" * 43)
        assert r.status_code == 400 and r.json()["error"] == "invalid_grant"
        assert r.headers["cache-control"] == "no-store"
        assert redeem(code, verifier).json()["error"] == "invalid_grant"

        # a DPoP-bound client must prove a key; a refused proof leaves the code usable
        code, verifier = new_code()
        assert redeem(code, verifier, dpop=None).json()["error"] == "invalid_dpop_proof"
        assert redeem(code, verifier, dpop=proof(url=ISS + "/elsewhere")).json()["error"] == (
            "invalid_dpop_proof"
        )
        r = redeem(code, verifier)
        assert r.status_code == 200, r.text
        tokens = r.json()
        assert tokens["token_type"] == "DPoP" and tokens["scope"] == "grants:read openid"
        assert r.headers["cache-control"] == "no-store"
        _, idt = verify_jws(tokens["id_token"], keys=jwks["keys"], typ="JWT")
        assert idt["aud"] == spa and idt["nonce"] == "n-1" and idt["sid"] == sid
        assert idt["iss"] == ISS and idt["sub"] == alice and idt["acr"] == "urn:zoiko:aal:z2"
        _, at = verify_jws(tokens["access_token"], keys=jwks["keys"], typ="at+jwt")
        assert at["cnf"] == {"jkt": jkt} and at["tenant_id"] == tenant and at["aud"] == AUD

        # the issued token authenticates at the resource server, only with its key (AUTH-06)
        path = f"/v1/authority-grants/{uid()}"
        acc = tokens["access_token"]
        api = AUD + path
        r = http.get(
            path,
            headers={"Authorization": f"DPoP {acc}", "DPoP": proof(url=api, method="GET", tok=acc)},
        )
        assert r.status_code == 404, r.text
        r = http.get(path, headers={"Authorization": f"Bearer {acc}"})
        assert r.status_code == 401

        # AUTH-03: replaying a redeemed code revokes the refresh family it produced
        assert redeem(code, verifier).json()["error"] == "invalid_grant"
        assert refresh(tokens["refresh_token"]).json()["error"] == "invalid_grant"

        # AUTH-07: rotation on every use; reuse revokes the family and the session and is signalled
        code, verifier = new_code()
        rt0 = redeem(code, verifier).json()["refresh_token"]
        assert refresh(rt0, LocalSigner("thief")).json()["error"] == "invalid_dpop_proof"
        assert refresh(rt0, scope="openid grants:write admin").json()["error"] == "invalid_scope"
        r = refresh(rt0, scope="grants:read")
        assert r.status_code == 200, r.text
        rt1 = r.json()["refresh_token"]
        assert rt1 != rt0 and r.json()["scope"] == "grants:read"
        assert refresh(rt0).json()["error"] == "invalid_grant"  # reuse
        assert refresh(rt1).json()["error"] == "invalid_grant"  # family is gone with it
        acc = r.json()["access_token"]
        r = http.get(
            path,
            headers={"Authorization": f"DPoP {acc}", "DPoP": proof(url=api, method="GET", tok=acc)},
        )
        assert r.status_code == 401 and r.json()["error"]["code"] == "SESSION_REVOKED"

        # AUTH-19: client revocation ends the family; unknown tokens answer success too
        sid2, handle2 = asyncio.run(new_session())
        code, verifier = new_code(cookie=handle2)
        rt = redeem(code, verifier).json()["refresh_token"]
        r = http.post("/oauth2/revoke", data={"client_id": spa, "token": rt}, headers=FORM)
        assert r.status_code == 200
        assert (
            http.post(
                "/oauth2/revoke", data={"client_id": spa, "token": "unknown"}, headers=FORM
            ).status_code
            == 200
        )
        assert refresh(rt).json()["error"] == "invalid_grant"
        r = http.post("/oauth2/revoke", data={"client_id": "nobody", "token": rt}, headers=FORM)
        assert r.status_code == 401 and r.json()["error"] == "invalid_client"

        # an expired code is refused
        code, verifier = new_code(cookie=handle2)

        async def expire_codes() -> None:
            async with engine(owner_url) as eng, eng.begin() as c:
                await c.execute(
                    text(
                        "UPDATE authn.authorization_code SET expires_at = now() - interval '1 second' "
                        "WHERE tenant_id = :t AND used_at IS NULL"
                    ),
                    {"t": uuid.UUID(tenant)},
                )

        asyncio.run(expire_codes())
        assert redeem(code, verifier).json()["error"] == "invalid_grant"

        # DPoP nonce (RFC 9449 §8): first proof is challenged, the retry with the nonce succeeds
        code, verifier = new_code(noncey, cookie=handle2)
        r = redeem(code, verifier, cid=noncey)
        assert r.status_code == 400 and r.json()["error"] == "use_dpop_nonce"
        nonce = r.headers["dpop-nonce"]
        r = redeem(code, verifier, cid=noncey, dpop=proof(nonce=nonce))
        assert r.status_code == 200, r.text
        assert r.headers["dpop-nonce"]

        # request hygiene: form encoding, known grants, known clients
        r = http.post("/oauth2/token", json={"grant_type": "authorization_code"})
        assert r.status_code == 400 and r.json()["error"] == "invalid_request"
        r = token({"grant_type": "password", "client_id": spa, "username": "a", "password": "b"})
        assert r.json()["error"] == "unsupported_grant_type"
        r = token({"grant_type": "refresh_token", "client_id": "nobody", "refresh_token": "x"})
        assert r.status_code == 401 and r.json()["error"] == "invalid_client"

        # a security-revision change ends refresh: the family is revoked, not continued (§8)
        code, verifier = new_code(cookie=handle2)
        rt = redeem(code, verifier).json()["refresh_token"]

        async def bump() -> None:
            async with engine(owner_url) as eng, eng.begin() as c:
                await c.execute(
                    text(
                        "UPDATE core.principal SET security_revision = security_revision + 1 "
                        "WHERE principal_id = :p"
                    ),
                    {"p": uuid.UUID(alice)},
                )

        asyncio.run(bump())
        assert refresh(rt).json()["error"] == "invalid_grant"

    async def state() -> tuple[dict[str, int], int, str, str]:
        async with engine(owner_url) as eng, eng.begin() as c:
            fams = dict(
                (
                    await c.execute(
                        text(
                            "SELECT coalesce(revoked_reason, status), count(*) "
                            "FROM authn.refresh_token_family WHERE tenant_id = :t GROUP BY 1"
                        ),
                        {"t": uuid.UUID(tenant)},
                    )
                ).all()
            )
            reuse = (
                await c.execute(
                    text(
                        "SELECT count(*) FROM events.outbox_event WHERE tenant_id = :t "
                        "AND event_type = 'com.zoiko.id.refresh_token.reuse_detected.v1'"
                    ),
                    {"t": uuid.UUID(tenant)},
                )
            ).scalar_one()
            s1 = (
                await c.execute(
                    text("SELECT status FROM authn.session WHERE session_id = :s"),
                    {"s": uuid.UUID(sid)},
                )
            ).scalar_one()
            s2 = (
                await c.execute(
                    text("SELECT status FROM authn.session WHERE session_id = :s"),
                    {"s": uuid.UUID(sid2)},
                )
            ).scalar_one()
            hashes = (
                await c.execute(
                    text(
                        "SELECT count(*) FROM authn.refresh_token WHERE tenant_id = :t "
                        "AND octet_length(token_hash) = 32"
                    ),
                    {"t": uuid.UUID(tenant)},
                )
            ).scalar_one()
            assert hashes >= 6
        return fams, reuse, s1, s2

    fams, reuse, s1, s2 = asyncio.run(state())
    assert fams == {
        "code_replay": 1,
        "refresh_reuse": 1,
        "client_revocation": 1,
        "SESSION_REAUTH_REQUIRED": 1,
        "ACTIVE": 1,  # the nonce client's family
    }, fams
    assert reuse == 1 and s1 == "REVOKED" and s2 == "REAUTH_REQUIRED"

    # RLS: another tenant cannot see this tenant's clients, codes or families
    async def isolated() -> int:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(uid())) as c:
            return (
                await c.execute(
                    text(
                        "SELECT (SELECT count(*) FROM authn.oauth_client) "
                        "+ (SELECT count(*) FROM authn.authorization_code) "
                        "+ (SELECT count(*) FROM authn.refresh_token_family)"
                    )
                )
            ).scalar_one()

    assert asyncio.run(isolated()) == 0
