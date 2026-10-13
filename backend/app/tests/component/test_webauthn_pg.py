"""Passkey registration, login, step-up and authenticator lifecycle end to end (ZID-AUTH-001 §3-5).

AUTH-01 (passkey happy path), AUTH-02 (origin/RP attack), AUTH-03 (challenge replay), AUTH-12
(step-up), AUTH-18 (no session fixation), plus the hand-off into the OAuth authorize endpoint.
A software authenticator produces real attestation objects and signatures.
"""

from __future__ import annotations

import asyncio
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlsplit

from conftest import ctx, engine
from fastapi.testclient import TestClient
from softauthn import SoftAuthenticator, b64
from sqlalchemy import text
from zid_crypto import KeyRing, LocalSigner
from zid_db import tenant_transaction
from zid_platform.main import create_app
from zid_platform.modules.authentication.infrastructure.authenticator_store import issue_ticket
from zid_platform.modules.principal.domain.principal import (
    Principal,
    PrincipalStatus,
    PrincipalType,
)
from zid_platform.modules.principal.infrastructure.principal_store import create_principal
from zid_platform.modules.session.domain.oauth import OAuthClient, pkce_challenge
from zid_platform.modules.session.infrastructure.oauth_store import create_client
from zid_platform.modules.session.ports import issue_access_token
from zid_platform.settings import Settings

ISS, AUD = "https://id.zoiko.test", "https://api.zoiko.test"
RP, ORIGIN = "id.zoiko.test", "https://id.zoiko.test"
RELATED = "https://accounts.zoiko-partner.test"  # FR-AUT-010 related origin
CB = "https://app.example/cb"
COOKIE = "__Host-zid_session"


def uid() -> str:
    return str(uuid.uuid4())


def test_passkey_registration_login_step_up_and_lifecycle(rt_url: str, owner_url: str) -> None:
    tenant, alice, mallory = uid(), uid(), uid()
    client_id = f"web-{uid()}"
    now = datetime.now(UTC)

    async def seed() -> tuple[str, str]:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            for p in (alice, mallory):
                await create_principal(
                    c, Principal(p, tenant, PrincipalType.HUMAN, PrincipalStatus.ACTIVE, "uk-1")
                )
            await create_client(
                c,
                OAuthClient(
                    client_id=client_id,
                    tenant_id=tenant,
                    client_type="PUBLIC",
                    profile="STANDARD",
                    redirect_uris=(CB,),
                    grant_types=frozenset({"authorization_code"}),
                    scopes=frozenset({"openid"}),
                    audiences=(AUD,),
                    token_auth_method="none",
                    dpop_bound=False,
                    status="ACTIVE",
                    first_party=True,
                ),
            )
            exp = now + timedelta(hours=1)
            t1 = await issue_ticket(
                c,
                tenant_id=tenant,
                principal_id=alice,
                purpose="BOOTSTRAP",
                issued_by="test",
                expires_at=exp,
            )
            t2 = await issue_ticket(
                c,
                tenant_id=tenant,
                principal_id=mallory,
                purpose="BOOTSTRAP",
                issued_by="test",
                expires_at=exp,
            )
            return t1, t2

    ticket, mallory_ticket = asyncio.run(seed())
    ring = KeyRing(LocalSigner("issuer-1"))
    app = create_app(
        Settings(
            database_url=rt_url,
            token_issuer=ISS,
            token_audience=AUD,
            public_base_url=AUD,
            environment="test",
            webauthn_rp_id=RP,
            webauthn_origins=(ORIGIN,),
            webauthn_related_origins=(RELATED,),
        ),
        keyring=ring,
    )
    h = {"Origin": ORIGIN}

    with TestClient(app, base_url=ISS, follow_redirects=False) as http:

        def post(path: str, body: dict[str, Any], cookie: str | None = None, **hdr: str) -> Any:
            http.cookies.clear()  # only the cookie a step names explicitly is sent
            headers = {**h, **hdr}
            if cookie:
                headers["Cookie"] = f"{COOKIE}={cookie}"
            return http.post(path, json={"client_id": client_id, **body}, headers=headers)

        def register(
            auth: SoftAuthenticator, *, ticket: str | None = None, cookie: str | None = None
        ) -> Any:
            body = {"enrollment_ticket": ticket} if ticket else {}
            opts = post("/authn/webauthn/registration/options", body, cookie)
            assert opts.status_code == 200, opts.text
            o = opts.json()
            assert (
                o["rp"]["id"] == RP
                and o["authenticatorSelection"]["userVerification"] == "required"
            )
            return post(
                "/authn/webauthn/registration/verify", {"credential": auth.create(o)}, cookie
            )

        def login(auth: SoftAuthenticator, **kw: str) -> Any:
            o = post("/authn/webauthn/authentication/options", {}).json()
            return post("/authn/webauthn/authentication/verify", {"credential": auth.get(o, **kw)})

        # AUTH-01: bootstrap enrollment with a one-time ticket, then passkey login
        alice_key = SoftAuthenticator(RP, ORIGIN)
        r = register(alice_key, ticket=ticket)
        assert r.status_code == 201, r.text
        assert r.json()["backup_eligible"] is True
        r = post("/authn/webauthn/registration/options", {"enrollment_ticket": ticket})
        assert r.status_code == 401  # the ticket is spent

        r = login(alice_key)
        assert r.status_code == 200, r.text
        s1 = r.json()
        assert s1["principal_id"] == alice and s1["aal"] == 2 and s1["amr"] == ["swk", "user"]
        cookie1 = r.cookies.get(COOKIE)
        set_cookie = r.headers["set-cookie"]
        assert cookie1 and "HttpOnly" in set_cookie and "Secure" in set_cookie
        assert "samesite=lax" in set_cookie.lower() and "Path=/" in set_cookie

        # AUTH-18: every login mints a new session and handle; a forged handle is useless
        s2 = login(alice_key)
        assert s2.json()["session_id"] != s1["session_id"]
        assert s2.cookies.get(COOKIE) != cookie1

        # AUTH-02: wrong origin in clientData, wrong RP id, cross-site request, unknown client
        r = login(alice_key, origin="https://evil.example")
        assert r.status_code == 401 and r.json()["error"]["code"] == "WEBAUTHN_VERIFICATION_FAILED"
        r = login(alice_key, rp_id="evil.example")
        assert r.status_code == 401
        o = post("/authn/webauthn/authentication/options", {}).json()
        r = post(
            "/authn/webauthn/authentication/verify",
            {"credential": alice_key.get(o)},
            Origin="https://evil.example",
        )
        assert r.status_code == 403 and r.json()["error"]["code"] == "CSRF_ORIGIN_REJECTED"
        r = http.post(
            "/authn/webauthn/authentication/options", json={"client_id": "nobody"}, headers=h
        )
        assert r.status_code == 400

        # AUTH-03: a challenge (and so an assertion) is single use
        o = post("/authn/webauthn/authentication/options", {}).json()
        cred = alice_key.get(o)
        assert (
            post("/authn/webauthn/authentication/verify", {"credential": cred}).status_code == 200
        )
        r = post("/authn/webauthn/authentication/verify", {"credential": cred})
        assert r.status_code == 401 and r.json()["error"]["code"] == "WEBAUTHN_CHALLENGE_INVALID"

        # an unregistered key, and a user who did not verify, never reach AAL-Z2
        assert login(SoftAuthenticator(RP, ORIGIN)).status_code == 401
        mallory_key = SoftAuthenticator(RP, ORIGIN, synced=False)
        assert register(mallory_key, ticket=mallory_ticket).status_code == 201
        mallory_key.user_verified = False
        r = login(mallory_key)
        assert r.status_code == 401  # UV is required for login
        mallory_key.user_verified = True
        r = login(mallory_key)
        assert r.json()["amr"] == ["hwk", "user"] and r.json()["aal"] == 2

        # counter anomaly: allowed, but capped at Z1 so anything needing Z2 must step up
        mallory_key.counter_step = 0
        r = login(mallory_key)
        assert r.status_code == 200 and r.json()["aal"] == 1 and r.json()["step_up_recommended"]

        # the session cookie hands off into the OAuth authorize endpoint
        verifier = secrets.token_urlsafe(48)
        r = http.get(
            "/oauth2/authorize",
            params={
                "client_id": client_id,
                "redirect_uri": CB,
                "response_type": "code",
                "state": "s",
                "scope": "openid",
                "nonce": "n",
                "code_challenge": pkce_challenge(verifier),
                "code_challenge_method": "S256",
            },
            headers={"Cookie": f"{COOKIE}={cookie1}"},
        )
        assert r.status_code == 303 and "code" in parse_qs(urlsplit(r.headers["location"]).query)

        # AUTH-12: step-up re-authenticates the same principal, rotates the session and handle
        r = post("/authn/step-up", {}, cookie1)
        assert r.status_code == 200, r.text
        o = r.json()
        assert [c["id"] for c in o["allowCredentials"]] == [b64(alice_key.credential_id)]
        r = post("/authn/webauthn/authentication/verify", {"credential": mallory_key.get(o)})
        assert r.status_code == 401  # another principal's key cannot satisfy alice's step-up
        r = post("/authn/step-up", {}, cookie1)
        r = post("/authn/webauthn/authentication/verify", {"credential": alice_key.get(r.json())})
        assert r.status_code == 200
        stepped = r.json()
        cookie3 = r.cookies.get(COOKIE)
        assert stepped["principal_id"] == alice and cookie3 != cookie1
        assert post("/authn/step-up", {}, cookie1).status_code == 401  # replaced session is gone

        # a second passkey can be added from a live session
        second = SoftAuthenticator(RP, ORIGIN)
        assert register(second, cookie=cookie3).status_code == 201
        r = post("/authn/webauthn/registration/options", {})
        assert r.status_code == 401 and r.json()["error"]["code"] == "SESSION_REQUIRED"

        def bearer(session_id: str, auth_time: datetime) -> dict[str, str]:
            tok = issue_access_token(
                ring,
                issuer=ISS,
                audience=AUD,
                subject=alice,
                tenant_id=tenant,
                client_id=client_id,
                now=datetime.now(UTC),
                session_id=session_id,
                auth_time=auth_time,
            )
            return {"Authorization": f"Bearer {tok}"}

        fresh = bearer(stepped["session_id"], datetime.now(UTC))
        listed = http.get("/authn/authenticators", headers=fresh).json()["items"]
        assert len(listed) == 2 and {a["status"] for a in listed} == {"ACTIVE"}
        sessions = http.get("/sessions", headers=fresh).json()["items"]
        assert stepped["session_id"] in {s["session_id"] for s in sessions}
        assert s1["session_id"] not in {s["session_id"] for s in sessions}  # rotated by step-up

        # protected removal: Z3 needed (the session is Z2, so it counts only while fresh)
        first_id = listed[0]["authenticator_id"]
        second_id = listed[1]["authenticator_id"]
        r = http.post(f"/authn/authenticators/{second_id}:revoke", headers=fresh)
        assert r.status_code == 200, r.text
        # the security revision moved: every session of alice now needs re-authentication
        r = http.get("/authn/authenticators", headers=fresh)
        assert r.status_code == 401 and r.json()["error"]["code"] == "SESSION_REAUTH_REQUIRED"
        assert login(second).status_code == 401  # the revoked passkey no longer logs in

        r = login(alice_key)
        fresh = bearer(r.json()["session_id"], datetime.now(UTC))
        r = http.post(f"/authn/authenticators/{first_id}:revoke", headers=fresh)
        assert r.status_code == 409 and r.json()["error"]["code"] == "LAST_STRONG_AUTHENTICATOR"

        # global logout revokes every live session of the caller
        r = http.post("/sessions:revoke-all", headers=fresh)
        assert r.status_code == 200 and r.json()["revoked"] >= 1
        assert http.get("/sessions", headers=fresh).status_code == 401

        # logout of the cookie session clears the cookie
        r = login(alice_key)
        cookie4 = r.cookies.get(COOKIE)
        r = post("/authn/logout", {}, cookie4)
        assert r.status_code == 204 and 'zid_session=""' in r.headers["set-cookie"]
        assert post("/authn/step-up", {}, cookie4).status_code == 401

        # FR-AUT-010: a governed related origin may use this RP ID; anything else may not
        assert http.get("/.well-known/webauthn").json() == {"origins": [ORIGIN, RELATED]}
        related_key = SoftAuthenticator(RP, RELATED)
        related_key.credential_id, related_key.key = alice_key.credential_id, alice_key.key
        related_key.user_handle, related_key.sign_count = (
            alice_key.user_handle,
            alice_key.sign_count,
        )
        o = post("/authn/webauthn/authentication/options", {}).json()
        r = post(
            "/authn/webauthn/authentication/verify",
            {"credential": related_key.get(o)},
            Origin=RELATED,
        )
        assert r.status_code == 200 and r.json()["principal_id"] == alice, r.text
        o = post("/authn/webauthn/authentication/options", {}).json()
        r = post(
            "/authn/webauthn/authentication/verify",
            {"credential": related_key.get(o, origin="https://unrelated.example")},
            Origin=RELATED,
        )
        assert r.status_code == 401

    async def facts() -> tuple[dict[str, int], int, int]:
        async with engine(owner_url) as eng, eng.begin() as c:
            events = dict(
                (
                    await c.execute(
                        text(
                            "SELECT event_type, count(*) FROM events.outbox_event "
                            "WHERE tenant_id = :t GROUP BY 1"
                        ),
                        {"t": uuid.UUID(tenant)},
                    )
                ).all()
            )
            rev = (
                await c.execute(
                    text("SELECT security_revision FROM core.principal WHERE principal_id = :p"),
                    {"p": uuid.UUID(alice)},
                )
            ).scalar_one()
            leaked = (
                await c.execute(
                    text(
                        "SELECT count(*) FROM authn.webauthn_challenge "
                        "WHERE tenant_id = :t AND expires_at > created_at + interval '5 minutes'"
                    ),
                    {"t": uuid.UUID(tenant)},
                )
            ).scalar_one()
        return events, rev, leaked

    events, rev, leaked = asyncio.run(facts())
    t = "com.zoiko.id.{}.v1".format
    assert events[t("authenticator.registered")] == 3
    assert events[t("authenticator.revoked")] == 1
    assert events[t("authentication.failed")] >= 5
    assert events[t("authentication.succeeded")] >= 8
    assert events[t("principal.security_revision.changed")] == 1
    assert rev == 1 and leaked == 0
