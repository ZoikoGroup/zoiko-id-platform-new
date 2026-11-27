"""CR-138: a federated sign-in whose client secret the company entered in the console (sealed by the platform) reaches the provider as that secret.

This goes through the real callback (`complete`), which closes its first database transaction before it calls the provider: the sealed secret must be read in a transaction of its own."""

from __future__ import annotations

import asyncio
import base64
import dataclasses
import json
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs

import httpx
from admin_fixture import make_admin
from conftest import ctx, engine
from fastapi.testclient import TestClient
from joserfc import jws
from sqlalchemy import text
from test_federation_oidc_pg import AUD, IDP, ISS, ORIGIN, FakeIdP, uid
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
from zid_platform.modules.session.domain.oauth import OAuthClient
from zid_platform.modules.session.domain.session import Session, SessionStatus
from zid_platform.modules.session.infrastructure.oauth_store import create_client
from zid_platform.modules.session.infrastructure.session_store import create_session
from zid_platform.modules.session.ports import issue_access_token
from zid_platform.settings import Settings

CLIENT_ID, SECRET = "auth0-app-client-id-123", "the-sealed-client-secret-0123456789"


class BasicIdP(FakeIdP):
    """The provider checks our client secret (HTTP Basic), the way Auth0 does."""

    def __init__(self, ring: KeyRing) -> None:
        super().__init__(ring, CLIENT_ID)
        self.auth_seen: str | None = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.auth_seen = request.headers.get("authorization")
        if self.auth_seen != "Basic " + base64.b64encode(f"{CLIENT_ID}:{SECRET}".encode()).decode():
            return httpx.Response(401, json={"error": "access_denied"})
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        now = int(time.time())
        claims = {"iss": IDP, "aud": CLIENT_ID, "sub": self.subject, "nonce": self.nonces[form["code"]], "iat": now, "exp": now + 300,
                  "auth_time": now - 10, "acr": "phr", "amr": ["hwk", "mfa"], "email": "alice@acme.example"}  # fmt: skip
        token = jws.serialize_compact(
            {"alg": "RS256", "kid": self.key.thumbprint(), "typ": "JWT"},
            json.dumps(claims).encode(),
            self.key,
            algorithms=["RS256"],
        )
        return httpx.Response(
            200, json={"id_token": token, "access_token": "x", "token_type": "Bearer"}
        )


def test_a_sealed_client_secret_signs_a_person_in_through_the_real_callback(
    rt_url: str, owner_url: str
) -> None:
    tenant, admin = uid(), uid()
    web = f"web-{uid()}"
    ring = KeyRing(LocalSigner("issuer-1"))
    idp = BasicIdP(ring)

    async def seed() -> str:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            await create_principal(
                c, Principal(admin, tenant, PrincipalType.HUMAN, PrincipalStatus.ACTIVE, "uk-1")
            )
            await c.execute(
                text(
                    "INSERT INTO platform.tenant (tenant_id, display_name, tenant_slug, status, deployment_mode, home_cell_id, creator_tenant_id, creator_principal_id, created_at, state_changed_at) "
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
            sid, now = uid(), datetime.now(UTC)
            principal, _ = await load_principal(c, admin)
            await create_session(
                c,
                Session(
                    sid,
                    tenant,
                    admin,
                    now - timedelta(seconds=20),
                    "native:webauthn",
                    2,
                    principal.security_revision,
                    1,
                    SessionStatus.ACTIVE,
                    now + timedelta(hours=8),
                    now,
                ),
            )
            return sid

    sid = asyncio.run(seed())
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
            session_id=sid,
        )
    }
    spec: dict[str, Any] = {
        "protocol": "OIDC", "display_name": "Auth0", "issuer": IDP, "client_id": CLIENT_ID, "client_auth": "client_secret_basic", "client_secret": SECRET,
        "authorization_endpoint": IDP + "/authorize", "token_endpoint": IDP + "/token", "jwks": idp.jwks(), "algorithms": ["RS256"], "acr_levels": {"phr": 2},
        "metadata_expires_at": (datetime.now(UTC) + timedelta(days=30)).isoformat(),
    }  # fmt: skip

    with TestClient(app, base_url=ISS, follow_redirects=False) as http:
        made = http.post("/federation/integrations", headers=adm, json=spec)
        assert made.status_code == 201, made.text
        cid = made.json()["connection_id"]
        r = http.get(f"/federation/connections/{cid}/login", params={"client_id": web})
        assert r.status_code == 303, r.text
        cb = idp.authorize(r.headers["location"])
        r = http.get(cb)
        # the provider accepted the sealed secret and the callback went on to the account step: an unlinked person is refused for being unlinked, not for a failed exchange
        assert idp.auth_seen is not None, "the token endpoint was never called"
        assert (
            r.status_code == 401 and r.json()["error"]["code"] == "FEDERATION_ACCOUNT_NOT_LINKED"
        ), r.text
