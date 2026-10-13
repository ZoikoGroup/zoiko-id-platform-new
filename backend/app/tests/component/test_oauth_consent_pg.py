"""Consent for third-party OAuth clients (ZID-AUTH-001 §6.2; ZID-PRIVACY-001).

A third-party client never gets a code without the user's recorded consent: the authorization
parks on a consent request bound to the session, the user decides in the consent UI, and a
consent covering the scopes skips the prompt next time. Withdrawing it ends the client's refresh
tokens.
"""

from __future__ import annotations

import asyncio
import secrets
import time
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

from conftest import ctx, engine
from fastapi.testclient import TestClient
from httpx import Response
from test_oauth_pg import AUD, CB, COOKIE, FORM, ISS, client, uid
from zid_crypto import KeyRing, LocalSigner, sign_jws
from zid_db import tenant_transaction
from zid_platform.main import create_app
from zid_platform.modules.principal.domain.principal import (
    Principal,
    PrincipalStatus,
    PrincipalType,
)
from zid_platform.modules.principal.infrastructure.principal_store import create_principal
from zid_platform.modules.session.domain.oauth import pkce_challenge
from zid_platform.modules.session.domain.session import Session, SessionStatus
from zid_platform.modules.session.domain.tokens import access_token_hash
from zid_platform.modules.session.infrastructure.oauth_store import create_client
from zid_platform.modules.session.infrastructure.session_store import create_session, issue_handle
from zid_platform.settings import Settings

CONSENT_UI = "https://id.zoiko.test/consent"


def test_third_party_consent_approve_deny_reuse_and_revoke(rt_url: str) -> None:
    tenant, alice = uid(), uid()
    third, first = f"third-{uid()}", f"spa-{uid()}"

    async def new_session() -> str:
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
            return await issue_handle(c, sid)

    async def seed() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            await create_principal(
                c, Principal(alice, tenant, PrincipalType.HUMAN, PrincipalStatus.ACTIVE, "uk-1")
            )
            await create_client(c, client(third, tenant, first_party=False))
            await create_client(c, client(first, tenant))

    asyncio.run(seed())
    handle, other = asyncio.run(new_session()), asyncio.run(new_session())
    app = create_app(
        Settings(
            database_url=rt_url,
            token_issuer=ISS,
            token_audience=AUD,
            public_base_url=AUD,
            environment="test",
            consent_url=CONSENT_UI,
            webauthn_rp_id="id.zoiko.test",
            webauthn_origins=[ISS],
        ),
        keyring=KeyRing(LocalSigner("issuer-1")),
    )
    key = LocalSigner("client-key")

    def proof(url: str, method: str = "POST", tok: str | None = None) -> str:
        body: dict[str, object] = {"htm": method, "htu": url, "jti": uid(), "iat": int(time.time())}
        if tok:
            body["ath"] = access_token_hash(tok)
        return sign_jws(key, body, typ="dpop+jwt", extra_header={"jwk": key.public_jwk()})

    with TestClient(app, base_url=ISS, follow_redirects=False) as http:

        def authorize(cid: str, verifier: str, cookie: str = handle, **params: str) -> Response:
            q = {
                "client_id": cid,
                "redirect_uri": CB,
                "response_type": "code",
                "state": "s-1",
                "scope": "openid grants:read",
                "nonce": "n-1",
                "code_challenge": pkce_challenge(verifier),
                "code_challenge_method": "S256",
                **params,
            }
            return http.get("/oauth2/authorize", params=q, headers={"Cookie": f"{COOKIE}={cookie}"})

        def query(location: str) -> dict[str, str]:
            return {k: v[0] for k, v in parse_qs(urlsplit(location).query).items()}

        def to_consent(r: Response) -> str:
            assert r.status_code == 303, r.text
            loc = r.headers["location"]
            assert loc.startswith(CONSENT_UI + "?"), loc
            return query(loc)["consent_request"]

        def show(ref: str, cookie: str | None = handle) -> Response:
            headers = {"Cookie": f"{COOKIE}={cookie}"} if cookie else {}
            return http.get(f"/oauth2/consent/{ref}", headers=headers)

        def decide(ref: str, decision: str, cookie: str = handle, origin: str = ISS) -> Response:
            return http.post(
                f"/oauth2/consent/{ref}",
                json={"decision": decision},
                headers={"Cookie": f"{COOKIE}={cookie}", "Origin": origin},
            )

        # a first-party client is never asked
        r = authorize(first, "f" * 43)
        assert r.status_code == 303 and "code" in query(r.headers["location"])

        # third party: parked on a consent request the UI can show, to this session only
        verifier = secrets.token_urlsafe(48)
        ref = to_consent(authorize(third, verifier))
        r = show(ref)
        assert r.status_code == 200, r.text
        assert r.json()["client_id"] == third
        assert set(r.json()["scopes"]) == {"openid", "grants:read"}
        assert r.headers["cache-control"] == "no-store"
        assert show(ref, other).status_code == 403
        assert show(ref, None).status_code == 404
        assert show(f"{tenant}.nope").status_code == 404
        # prompt=none cannot show the UI
        r = authorize(third, verifier, prompt="none")
        assert query(r.headers["location"])["error"] == "consent_required"

        # the decision: CSRF origin checked; another session is refused without burning it
        assert decide(ref, "approve", origin="https://evil.example").status_code == 403
        assert decide(ref, "approve", cookie=other).status_code == 403
        r = decide(ref, "approve")
        assert r.status_code == 200, r.text
        q = query(r.json()["redirect_to"])
        assert q["state"] == "s-1" and q["iss"] == ISS and "code" in q
        assert decide(ref, "approve").status_code == 404  # single use

        # the code redeems normally
        r = http.post(
            "/oauth2/token",
            data={
                "grant_type": "authorization_code",
                "client_id": third,
                "code": q["code"],
                "redirect_uri": CB,
                "code_verifier": verifier,
            },
            headers={**FORM, "DPoP": proof(ISS + "/oauth2/token")},
        )
        assert r.status_code == 200, r.text
        tokens = r.json()

        # covered next time (a subset too); a wider scope or prompt=consent asks again
        r = authorize(third, "a" * 43, scope="openid")
        assert "code" in query(r.headers["location"])
        to_consent(authorize(third, "a" * 43, scope="openid grants:write"))
        to_consent(authorize(third, "a" * 43, prompt="consent"))

        # deny: the client learns only access_denied
        ref = to_consent(authorize(third, "a" * 43, scope="openid grants:write"))
        q = query(decide(ref, "deny").json()["redirect_to"])
        assert q["error"] == "access_denied" and q["state"] == "s-1"

        # the user sees and withdraws consents; the client's refresh tokens end with it
        acc = tokens["access_token"]

        def api(method: str, path: str) -> Response:
            return http.request(
                method,
                path,
                headers={"Authorization": f"DPoP {acc}", "DPoP": proof(AUD + path, method, acc)},
            )

        r = api("GET", "/oauth2/consents")
        assert r.status_code == 200, r.text
        assert [c["client_id"] for c in r.json()["items"]] == [third]
        r = api("POST", f"/oauth2/consents/{third}:revoke")
        assert r.status_code == 200, r.text
        assert api("POST", f"/oauth2/consents/{third}:revoke").status_code == 404
        r = http.post(
            "/oauth2/token",
            data={
                "grant_type": "refresh_token",
                "client_id": third,
                "refresh_token": tokens["refresh_token"],
            },
            headers={**FORM, "DPoP": proof(ISS + "/oauth2/token")},
        )
        assert r.json()["error"] == "invalid_grant"
        to_consent(authorize(third, "a" * 43, scope="openid"))
