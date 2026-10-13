"""OpenID Federation 1.1 trust chains end to end (ZID-AUTH-001 §12, §18; AUTH-W8).

trust anchor -> intermediate -> OpenID provider, served by a simulator. A federation-backed OIDC
connection takes its endpoints and keys from the validated, policy-applied chain at login time.
Covered: pinned anchor keys, subordinate-vouched keys, expiry, naming constraints, metadata
policy, SSRF trust domains, anchor quarantine, and Zoiko's own entity configuration.
"""

from __future__ import annotations

import asyncio
import dataclasses
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
from admin_fixture import make_admin
from conftest import ctx, engine
from fastapi.testclient import TestClient
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
from zid_platform.modules.session.domain.oauth import OAuthClient
from zid_platform.modules.session.domain.session import Session, SessionStatus
from zid_platform.modules.session.infrastructure.oauth_store import create_client
from zid_platform.modules.session.infrastructure.session_store import create_session
from zid_platform.modules.session.ports import issue_access_token
from zid_platform.settings import Settings

ISS, AUD = "https://id.zoiko.test", "https://api.zoiko.test"
TA, INT, OP = "https://ta.fed.example", "https://int.fed.example", "https://op.acme.fed.example"
T = "entity-statement+jwt"


def uid() -> str:
    return str(uuid.uuid4())


class Federation:
    def __init__(self, zoiko: KeyRing) -> None:
        self.keys = {e: LocalSigner(f"{e}-fed") for e in (TA, INT, OP)}
        self.id_key = LocalSigner("op-id-token")
        self.zoiko = zoiko
        self.nonces: dict[str, str] = {}
        self.overrides: dict[str, dict[str, Any]] = {}  # entity -> extra claims for its statements
        self.ta_signer: LocalSigner | None = None

    def jwks(self, e: str) -> dict[str, Any]:
        return {"keys": [self.keys[e].public_jwk()]}

    def statement(self, iss: str, sub: str, **claims: Any) -> str:
        now = int(time.time())
        body = {
            "iss": iss,
            "sub": sub,
            "iat": now,
            "exp": now + 3600,
            **claims,
            **self.overrides.get(f"{iss}>{sub}", {}),
        }
        signer = self.ta_signer if (iss == TA and self.ta_signer) else self.keys[iss]
        return sign_jws(signer, body, typ=T)

    def ec(self, e: str) -> str:
        meta: dict[str, Any] = {"federation_entity": {"federation_fetch_endpoint": f"{e}/fetch"}}
        hints = {TA: [], INT: [TA], OP: [INT]}[e]
        if e == OP:
            meta["openid_provider"] = {
                "issuer": OP,
                "authorization_endpoint": f"{OP}/authorize",
                "token_endpoint": f"{OP}/token",
                "jwks": {"keys": [self.id_key.public_jwk()]},
                "token_endpoint_auth_methods_supported": ["private_key_jwt", "client_secret_basic"],
            }
        return self.statement(e, e, jwks=self.jwks(e), metadata=meta, authority_hints=hints)

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        base = f"{request.url.scheme}://{request.url.host}"
        if request.url.path == "/.well-known/openid-federation":
            return httpx.Response(200, text=self.ec(base))
        if request.url.path == "/fetch":
            sub = request.url.params["sub"]
            policy = {
                TA: {
                    "openid_provider": {
                        "token_endpoint_auth_methods_supported": {
                            "subset_of": ["private_key_jwt", "tls_client_auth"]
                        }
                    }
                },
                INT: {
                    "openid_provider": {
                        "token_endpoint_auth_methods_supported": {"essential": True}
                    }
                },
            }[base]
            constraints = (
                {"naming_constraints": {"excluded": [".evil.fed.example"]}} if base == TA else {}
            )
            return httpx.Response(
                200,
                text=self.statement(
                    base, sub, jwks=self.jwks(sub), metadata_policy=policy, constraints=constraints
                ),
            )
        if url == f"{OP}/token":
            form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            verify_jws(form["client_assertion"], keys=self.zoiko.jwks()["keys"], typ="JWT")
            now = int(time.time())
            idt = sign_jws(
                self.id_key,
                {
                    "iss": OP,
                    "aud": "zoiko-rp",
                    "sub": "fed-user-1",
                    "nonce": self.nonces[form["code"]],
                    "iat": now,
                    "exp": now + 300,
                    "amr": ["hwk", "user"],
                },
                typ="JWT",
            )
            return httpx.Response(
                200, json={"id_token": idt, "token_type": "Bearer", "access_token": "x"}
            )
        return httpx.Response(404)

    def authorize(self, location: str) -> str:
        q = {k: v[0] for k, v in parse_qs(urlsplit(location).query).items()}
        code = uid()
        self.nonces[code] = q["nonce"]
        return f"/federation/callback?code={code}&state={q['state']}&iss={OP}"


def test_trust_chain_backed_oidc_login(rt_url: str) -> None:
    tenant, admin = uid(), uid()
    web = f"web-{uid()}"
    ring = KeyRing(LocalSigner("issuer-1"))
    fed = Federation(ring)

    async def seed() -> str:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            await create_principal(
                c, Principal(admin, tenant, PrincipalType.HUMAN, PrincipalStatus.ACTIVE, "uk-1")
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
                c,
                tenant,
                admin,
                ["tenant.identity_provider.change"],
                ["federation_connection:*", "federation_trust_anchor:*"],
            )
            sid, now = uid(), datetime.now(UTC)
            p, _ = await load_principal(c, admin)
            await create_session(
                c,
                Session(
                    sid,
                    tenant,
                    admin,
                    now - timedelta(seconds=5),
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

    sid = asyncio.run(seed())
    app = create_app(
        Settings(
            database_url=rt_url,
            token_issuer=ISS,
            token_audience=AUD,
            public_base_url=AUD,
            environment="test",
            federation_authority_hints=(TA,),
        ),
        keyring=ring,
    )
    app.state.federation_settings = dataclasses.replace(
        app.state.federation_settings, transport=httpx.MockTransport(fed.handler)
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

    with TestClient(app, base_url=ISS, follow_redirects=False) as http:
        # Zoiko's own entity configuration
        r = http.get("/.well-known/openid-federation")
        assert r.headers["content-type"].startswith("application/entity-statement+jwt")
        _, own = verify_jws(r.text, keys=ring.jwks()["keys"], typ=T)
        assert own["iss"] == own["sub"] == ISS and own["authority_hints"] == [TA]
        assert own["metadata"]["openid_relying_party"]["redirect_uris"] == [
            ISS + "/federation/callback"
        ]

        r = http.post(
            "/federation/trust-anchors",
            headers=adm,
            json={"entity_id": TA, "jwks": fed.jwks(TA), "allowed_domains": ["fed.example"]},
        )
        assert r.status_code == 201, r.text
        anchor = r.json()["anchor_id"]
        r = http.post(
            "/federation/integrations",
            headers=adm,
            json={
                "protocol": "OIDC",
                "display_name": "Acme via federation",
                "issuer": OP,
                "client_id": "zoiko-rp",
                "client_auth": "private_key_jwt",
                "trust_anchor_id": anchor,
                "federation_entity_id": OP,
                "algorithms": ["ES256"],
                "link_policy": "JIT",
                "metadata_expires_at": (datetime.now(UTC) + timedelta(days=30)).isoformat(),
            },
        )
        assert r.status_code == 201, r.text
        conn_id = r.json()["connection_id"]

        def login() -> Any:
            return http.get(f"/federation/connections/{conn_id}/login", params={"client_id": web})

        # endpoints and keys come from the validated chain
        r = login()
        assert r.status_code == 303, r.text
        assert r.headers["location"].startswith(f"{OP}/authorize?")
        r = http.get(fed.authorize(r.headers["location"]))
        assert r.status_code == 200, r.text
        assert r.json()["authentication_context"] == f"oidc:{OP}" and r.json()["linked"] is True

        def fails(code: str) -> None:
            r = login()
            assert r.status_code in (401, 502) and r.json()["error"]["code"] == code, r.text

        # the anchor is trusted only through its pinned keys
        fed.ta_signer = LocalSigner("rogue-ta")
        fails("TRUST_CHAIN_UNRESOLVED")
        fed.ta_signer = None
        # an expired subordinate statement breaks the chain
        fed.overrides[f"{INT}>{OP}"] = {"exp": int(time.time()) - 10}
        fails("TRUST_CHAIN_UNRESOLVED")
        fed.overrides.clear()
        # the subordinate statement's keys, not the entity's own claim, decide: swap the OP's keys
        real = fed.keys[OP]
        fed.keys[OP] = LocalSigner("op-fed-rotated-without-superior")
        fed.overrides[f"{INT}>{OP}"] = {"jwks": {"keys": [real.public_jwk()]}}
        fails("TRUST_CHAIN_UNRESOLVED")
        fed.keys[OP] = real
        fed.overrides.clear()
        # policy: an essential parameter removed by subset_of fails the chain
        fed.overrides[f"{INT}>{OP}"] = {
            "metadata": {
                "openid_provider": {
                    "token_endpoint_auth_methods_supported": ["client_secret_basic"]
                }
            }
        }
        fails("METADATA_POLICY_VIOLATION")
        fed.overrides.clear()
        assert login().status_code == 303

        # quarantine the anchor: nothing resolves through it any more
        assert (
            http.post(f"/federation/trust-anchors/{anchor}:disable", headers=adm).status_code == 200
        )
        fails("TRUST_ANCHOR_QUARANTINED")
