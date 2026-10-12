"""Request authentication end to end (ZID-AUTH-001 §5, §6.3, §7.1, §16; RFC 9068/9449; INV-13).

Real tokens, real DPoP proofs, real sessions in PostgreSQL with RLS: no dependency override.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import UTC, datetime, timedelta

from conftest import ctx, engine
from fastapi.testclient import TestClient
from sqlalchemy import text
from zid_crypto import KeyRing, LocalSigner, jwk_thumbprint, sign_jws
from zid_db import tenant_transaction
from zid_platform.main import create_app
from zid_platform.modules.principal.domain.principal import (
    Principal,
    PrincipalStatus,
    PrincipalType,
)
from zid_platform.modules.principal.infrastructure.principal_store import create_principal
from zid_platform.modules.session.domain.session import Session, SessionStatus
from zid_platform.modules.session.domain.tokens import access_token_hash
from zid_platform.modules.session.infrastructure.session_store import create_session
from zid_platform.modules.session.ports import issue_access_token
from zid_platform.settings import Settings

ISS, AUD, BASE = "https://id.zoiko.test", "https://api.zoiko.test", "https://api.zoiko.test"


def uid() -> str:
    return str(uuid.uuid4())


def test_tokens_dpop_and_sessions_over_http(rt_url: str, owner_url: str) -> None:
    tenant, alice = uid(), uid()
    sid = uid()
    now = datetime.now(UTC)

    async def seed() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            await create_principal(
                c, Principal(alice, tenant, PrincipalType.HUMAN, PrincipalStatus.ACTIVE, "uk-1")
            )
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
                    2,
                    SessionStatus.ACTIVE,
                    now + timedelta(hours=8),
                ),
            )

    asyncio.run(seed())
    ring = KeyRing(LocalSigner("issuer-1"))
    app = create_app(
        Settings(
            database_url=rt_url,
            token_issuer=ISS,
            token_audience=AUD,
            public_base_url=BASE,
            environment="test",
        ),
        keyring=ring,
    )
    client_key = LocalSigner("client-key")
    jkt = jwk_thumbprint(client_key.public_jwk())

    def token(**kw: object) -> str:
        args: dict[str, object] = {
            "issuer": ISS,
            "audience": AUD,
            "subject": alice,
            "tenant_id": tenant,
            "client_id": "web",
            "now": datetime.now(UTC),
            "session_id": sid,
            "dpop_jkt": jkt,
            "amr": ("hwk", "user"),
        }
        return issue_access_token(ring, **{**args, **kw})  # type: ignore[arg-type]

    def proof(
        tok: str, path: str, method: str = "GET", key: LocalSigner = client_key, **kw: object
    ) -> str:
        body = {
            "htm": method,
            "htu": BASE + path,
            "jti": uid(),
            "iat": int(time.time()),
            "ath": access_token_hash(tok),
        }
        return sign_jws(key, {**body, **kw}, typ="dpop+jwt", extra_header={"jwk": key.public_jwk()})

    path = f"/v1/authority-grants/{uid()}"  # a protected read; 404 means "authenticated, not found"
    with TestClient(app, base_url=BASE) as http:
        r = http.get(path)
        assert r.status_code == 401 and r.json()["error"]["code"] == "UNAUTHENTICATED"
        assert r.headers["www-authenticate"].startswith("Bearer realm=")

        tok = token()
        r = http.get(path, headers={"Authorization": f"DPoP {tok}", "DPoP": proof(tok, path)})
        assert r.status_code == 404, (
            r.text
        )  # authenticated: tenant and principal came from the token

        # a key-bound token is never downgraded to a bearer token
        r = http.get(path, headers={"Authorization": f"Bearer {tok}"})
        assert r.json()["error"]["code"] == "DPOP_PROOF_REQUIRED"
        assert 'error="invalid_token"' in r.headers["www-authenticate"]

        # replayed proof, proof from another key, proof for another URL or method
        p = proof(tok, path)
        assert (
            http.get(path, headers={"Authorization": f"DPoP {tok}", "DPoP": p}).status_code == 404
        )
        r = http.get(path, headers={"Authorization": f"DPoP {tok}", "DPoP": p})
        assert r.json()["error"]["code"] == "DPOP_PROOF_REPLAYED"
        r = http.get(
            path,
            headers={
                "Authorization": f"DPoP {tok}",
                "DPoP": proof(tok, path, key=LocalSigner("x")),
            },
        )
        assert r.json()["error"]["code"] == "DPOP_KEY_MISMATCH"
        r = http.get(
            path, headers={"Authorization": f"DPoP {tok}", "DPoP": proof(tok, "/v1/other")}
        )
        assert r.json()["error"]["code"] == "DPOP_PROOF_INVALID"
        r = http.get(
            path, headers={"Authorization": f"DPoP {tok}", "DPoP": proof(tok, path, method="POST")}
        )
        assert r.json()["error"]["code"] == "DPOP_PROOF_INVALID"
        r = http.get(path, headers={"Authorization": f"DPoP {tok}", "DPoP": proof(token(), path)})
        assert (
            r.json()["error"]["code"] == "DPOP_PROOF_INVALID"
        )  # ath binds the proof to this token

        # tokens from another issuer key, or for another audience, are refused
        forged = issue_access_token(
            KeyRing(LocalSigner("issuer-1")),
            issuer=ISS,
            audience=AUD,
            subject=alice,
            tenant_id=tenant,
            client_id="web",
            now=datetime.now(UTC),
            session_id=sid,
        )
        assert (
            http.get(path, headers={"Authorization": f"Bearer {forged}"}).json()["error"]["code"]
            == "TOKEN_INVALID"
        )
        other_aud = token(audience="https://other.api", dpop_jkt=None)
        r = http.get(path, headers={"Authorization": f"Bearer {other_aud}"})
        assert r.json()["error"]["code"] == "TOKEN_AUDIENCE_INVALID"

        # an unbound token works as a bearer token, but not under the DPoP scheme
        bearer = token(dpop_jkt=None)
        assert http.get(path, headers={"Authorization": f"Bearer {bearer}"}).status_code == 404
        r = http.get(path, headers={"Authorization": f"DPoP {bearer}", "DPoP": proof(bearer, path)})
        assert r.json()["error"]["code"] == "TOKEN_NOT_SENDER_CONSTRAINED"

        # a token naming another tenant's session id cannot use it
        r = http.get(
            path, headers={"Authorization": f"Bearer {token(dpop_jkt=None, tenant_id=uid())}"}
        )
        assert r.json()["error"]["code"] == "SESSION_INVALID"

        # security revision moves (e.g. authenticator revoked): the session needs re-authentication, at once
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
        r = http.get(path, headers={"Authorization": f"Bearer {bearer}"})
        assert r.status_code == 401 and r.json()["error"]["code"] == "SESSION_REAUTH_REQUIRED"

    async def state() -> tuple[str, int]:
        async with engine(owner_url) as eng, eng.begin() as c:
            st = (
                await c.execute(
                    text("SELECT status FROM authn.session WHERE session_id = :s"),
                    {"s": uuid.UUID(sid)},
                )
            ).scalar_one()
            events = (
                await c.execute(
                    text("SELECT count(*) FROM events.outbox_event WHERE aggregate_id = :s"),
                    {"s": sid},
                )
            ).scalar_one()
        return st, events

    status, events = asyncio.run(state())
    assert status == "REAUTH_REQUIRED"  # persisted although the request failed
    assert events == 1  # session.created

    # without a configured issuer nothing authenticates
    plain = create_app(Settings(database_url=rt_url))
    with TestClient(plain, base_url=BASE) as http:
        assert http.get(path, headers={"Authorization": f"Bearer {bearer}"}).status_code == 401
