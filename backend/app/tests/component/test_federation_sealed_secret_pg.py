"""CR-138: the client secret a company enters in the console, sealed by the platform."""

from __future__ import annotations

import asyncio
import dataclasses
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from admin_fixture import make_admin
from conftest import ctx, engine
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from fastapi.testclient import TestClient
from sqlalchemy import text
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
from zid_platform.modules.session.domain.session import Session, SessionStatus
from zid_platform.modules.session.infrastructure.session_store import create_session
from zid_platform.modules.session.ports import issue_access_token
from zid_platform.settings import Settings

ISS, AUD = "https://id.zoiko.test", "https://api.zoiko.test"
IDP = "https://login.acme.example"
JWKS = {"keys": [{"kty": "RSA", "kid": "k1", "n": "AQAB", "e": "AQAB", "use": "sig"}]}


def uid() -> str:
    return str(uuid.uuid4())


def cert_pem() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "acme-idp")])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=60))
        .sign(key, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.PEM).decode()


def soon(days: int = 30) -> str:
    return (datetime.now(UTC) + timedelta(days=days)).isoformat()


def test_a_company_gives_the_client_secret_in_the_console_and_it_is_sealed_write_only(rt_url: str, owner_url: str) -> None:
    """CR-138: the secret is entered once, never returned, kept as a sealed connector credential, and replaced by rotating with a new one. No operator secret store is involved."""
    tenant, admin = uid(), uid()
    web = f"web-{uid()}"
    ring = KeyRing(LocalSigner("issuer-1"))

    async def seed() -> str:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            await create_principal(c, Principal(admin, tenant, PrincipalType.HUMAN, PrincipalStatus.ACTIVE, "uk-1"))
            await make_admin(c, tenant, admin, ["tenant.identity_provider.change"], ["federation_connection:*", "federation_trust_anchor:*"])
            sid, now = uid(), datetime.now(UTC)
            principal, _ = await load_principal(c, admin)
            await create_session(
                c,
                Session(sid, tenant, admin, now - timedelta(seconds=20), "native:webauthn", 2, principal.security_revision, 1, SessionStatus.ACTIVE, now + timedelta(hours=8), now),
            )
            return sid

    sid = asyncio.run(seed())
    app = create_app(
        Settings(database_url=rt_url, token_issuer=ISS, token_audience=AUD, public_base_url=AUD, environment="test", webauthn_rp_id="id.zoiko.test", webauthn_origins=(ISS,)),
        keyring=ring,
    )
    app.state.federation_settings = dataclasses.replace(app.state.federation_settings)
    adm = {
        "Authorization": "Bearer "
        + issue_access_token(ring, issuer=ISS, audience=AUD, subject=admin, tenant_id=tenant, client_id=web, now=datetime.now(UTC), session_id=sid)
    }
    SECRET1, SECRET2 = "s3cr3t-value-one-0123456789", "s3cr3t-value-two-9876543210"
    body = {
        "protocol": "OIDC", "display_name": "Auth0", "issuer": IDP, "metadata_expires_at": soon(), "client_id": "client-abc-12345",
        "client_auth": "client_secret_basic", "client_secret": SECRET1, "authorization_endpoint": f"{IDP}/authorize",
        "token_endpoint": f"{IDP}/token", "jwks": JWKS,
    }  # fmt: skip

    def refs(conn_id: str) -> str:
        async def q() -> str:
            async with engine(owner_url) as eng, eng.connect() as c:
                return (await c.execute(text("SELECT client_secret_ref FROM authn.federation_connection WHERE connection_id = :i"), {"i": uuid.UUID(conn_id)})).scalar_one()

        return asyncio.run(q())

    def secret_of(ref: str) -> str | None:
        from zid_platform.modules.integration.ports import connector_secret

        async def q() -> str | None:
            async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
                return await connector_secret(c, tenant, ref.split(":", 1)[1], datetime.now(UTC))

        return asyncio.run(q())

    with TestClient(app) as http:
        # a secret together with the name of a stored one, or with a signed key, is refused
        both = http.post("/federation/integrations", headers=adm, json={**body, "client_secret_ref": "some-name"})
        assert both.status_code in (400, 422), both.text
        bad = http.post("/federation/integrations", headers=adm, json={**body, "client_auth": "private_key_jwt"})
        assert bad.status_code in (400, 422), bad.text

        made = http.post("/federation/integrations", headers=adm, json=body)
        assert made.status_code == 201, made.text
        assert SECRET1 not in made.text, "the secret is never returned"
        cid = made.json()["connection_id"]
        ref = refs(cid)
        assert ref.startswith("connector:") and SECRET1 not in ref
        assert secret_of(ref) == SECRET1  # sealed, readable only by the connection's own runtime

        again = http.get("/federation/integrations", headers=adm)
        assert SECRET1 not in again.text

        # rotating with a new secret replaces it at once, under the same reference
        rot = http.post(f"/federation/integrations/{cid}:rotate", headers=adm, json={"metadata_expires_at": soon(60), "client_secret": SECRET2})
        assert rot.status_code == 200, rot.text
        assert SECRET2 not in rot.text
        assert refs(cid) == ref
        assert secret_of(ref) == SECRET2
