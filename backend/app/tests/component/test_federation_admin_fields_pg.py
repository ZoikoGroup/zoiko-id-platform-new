"""Identity-provider administration, field by field: what is typed is validated, stored in its column, and shown back.

Covers create (OIDC with every field, SAML, federation-resolved), the readable profile errors, duplicate issuer / anchor,
rotate, disable (idempotent) and trust anchors, against real PostgreSQL with RLS in force.
"""

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


def test_identity_provider_fields_round_trip(rt_url: str, owner_url: str) -> None:
    tenant, admin = uid(), uid()
    web = f"web-{uid()}"
    ring = KeyRing(LocalSigner("issuer-1"))

    async def seed() -> str:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            await create_principal(
                c, Principal(admin, tenant, PrincipalType.HUMAN, PrincipalStatus.ACTIVE, "uk-1")
            )
            await make_admin(
                c,
                tenant,
                admin,
                ["tenant.identity_provider.change"],
                ["federation_connection:*", "federation_trust_anchor:*"],
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
            webauthn_origins=(ISS,),
        ),
        keyring=ring,
    )
    app.state.federation_settings = dataclasses.replace(app.state.federation_settings)
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

    def row(conn_id: str) -> dict[str, Any]:
        async def q() -> dict[str, Any]:
            async with engine(owner_url) as eng, eng.connect() as c:
                r = await c.execute(
                    text("SELECT * FROM authn.federation_connection WHERE connection_id = :i"),
                    {"i": uuid.UUID(conn_id)},
                )
                return dict(r.one()._mapping)

        return asyncio.run(q())

    def arow(anchor_id: str) -> dict[str, Any]:
        async def q() -> dict[str, Any]:
            async with engine(owner_url) as eng, eng.connect() as c:
                r = await c.execute(
                    text("SELECT * FROM authn.federation_trust_anchor WHERE anchor_id = :i"),
                    {"i": uuid.UUID(anchor_id)},
                )
                return dict(r.one()._mapping)

        return asyncio.run(q())

    with TestClient(app, base_url=ISS, follow_redirects=False) as http:
        oidc = {
            "protocol": "OIDC",
            "display_name": "  Acme Entra  ",
            "issuer": IDP,
            "client_id": "zoiko-at-acme",
            "client_auth": "client_secret_basic",
            "client_secret_ref": "secret/acme",
            "authorization_endpoint": IDP + "/authorize",
            "token_endpoint": IDP + "/token",
            "jwks": JWKS,
            "algorithms": ["RS256", "RS256", "ES256"],
            "scopes": ["email", " profile ", "email"],
            "acr_levels": {"phr": 5, "mfa": 1},
            "trust_amr": False,
            "link_policy": "JIT",
            "metadata_expires_at": soon(),
            "sp_entity_id": "",
        }
        r = http.post("/federation/integrations", headers=adm, json=oidc)
        assert r.status_code == 201, r.text
        made = r.json()
        cid = made["connection_id"]
        # every field comes back, normalised
        assert made["display_name"] == "Acme Entra" and made["client_id"] == "zoiko-at-acme"
        assert made["client_auth"] == "client_secret_basic"
        assert made["client_secret_ref"] == "secret/acme"
        assert made["scopes"] == ["openid", "email", "profile"]
        assert made["algorithms"] == ["RS256", "ES256"]
        assert made["acr_levels"] == {"phr": 2, "mfa": 1}  # never above Z2
        assert made["trust_amr"] is False and made["link_policy"] == "JIT"
        assert made["authorization_endpoint"] == IDP + "/authorize"
        assert made["token_endpoint"] == IDP + "/token" and made["pinned_keys"] == 1
        # ... and sits in its column
        db = row(cid)
        assert db["display_name"] == "Acme Entra" and db["client_id"] == "zoiko-at-acme"
        assert (
            db["client_auth"] == "client_secret_basic" and db["client_secret_ref"] == "secret/acme"
        )
        assert list(db["scopes"]) == ["openid", "email", "profile"]
        assert list(db["algorithms"]) == ["RS256", "ES256"]
        assert db["acr_levels"] == {"phr": 2, "mfa": 1} and db["trust_amr"] is False
        assert db["link_policy"] == "JIT" and db["jwks"] == JWKS and db["sp_entity_id"] is None
        assert db["authorization_endpoint"] == IDP + "/authorize" and db["status"] == "ACTIVE"

        listed = {
            i["connection_id"]: i
            for i in http.get("/federation/integrations", headers=adm).json()["items"]
        }
        assert listed[cid] == made

        # readable profile errors, nothing stored
        def bad(**over: Any) -> Any:
            spec = {**oidc, "issuer": IDP + "/" + uid(), **over}
            r = http.post("/federation/integrations", headers=adm, json=spec)
            assert r.status_code in (409, 422), r.text
            return r

        dup = http.post("/federation/integrations", headers=adm, json=oidc)
        assert dup.status_code == 409 and dup.json()["error"]["code"] == "FEDERATION_ISSUER_EXISTS"
        assert "client_id" in bad(client_id=None).text
        assert "client_secret_ref" in bad(client_secret_ref=None).text
        assert "pin at least one key" in bad(jwks={"keys": []}).text
        assert "SAML settings" in bad(sso_url="https://x.example/sso").text
        assert "time zone" in bad(metadata_expires_at="2030-01-01T00:00:00").text
        bad(display_name="   ")
        bad(acr_levels={"phr": -1})
        bad(algorithms=[])
        bad(scopes=["has space"])
        assert "refused" in bad(token_endpoint="https://127.0.0.1/token").text
        assert (
            "active trust anchor"
            in bad(
                authorization_endpoint=None,
                token_endpoint=None,
                jwks=None,
                trust_anchor_id=uid(),
                federation_entity_id="https://op.example",
            ).text
        )
        assert "go together" in bad(trust_anchor_id=uid()).text

        # SAML: certificates are real, the ACS address is shown, OIDC-only settings are refused
        pem = cert_pem()
        saml = {
            "protocol": "SAML",
            "display_name": "Acme ADFS",
            "issuer": "https://idp.acme.example/saml",
            "sso_url": "https://saml.acme.example/sso",
            "idp_certificates": [pem],
            "sp_entity_id": AUD + "/saml/sp",
            "require_encrypted_assertions": True,
            "sp_decryption_key_ref": "secret/sp-key",
            "metadata_expires_at": soon(),
        }
        r = http.post(
            "/federation/integrations",
            headers=adm,
            json={**saml, "idp_certificates": ["not a cert"]},
        )
        assert r.status_code == 422 and "X.509" in r.text
        r = http.post("/federation/integrations", headers=adm, json={**saml, "client_id": "x"})
        assert r.status_code == 422 and "OIDC settings" in r.text
        r = http.post(
            "/federation/integrations", headers=adm, json={**saml, "sp_decryption_key_ref": None}
        )
        assert r.status_code == 422 and "sp_decryption_key_ref" in r.text
        r = http.post("/federation/integrations", headers=adm, json=saml)
        assert r.status_code == 201, r.text
        s = r.json()
        assert s["acs_url"] == ISS + "/federation/saml/acs" and s["sso_url"] == saml["sso_url"]
        assert s["sp_entity_id"] == saml["sp_entity_id"] and s["pinned_certificates"] == 1
        assert (
            s["require_encrypted_assertions"] is True
            and s["sp_decryption_key_ref"] == "secret/sp-key"
        )
        dbs = row(s["connection_id"])
        assert dbs["sso_url"] == saml["sso_url"] and list(dbs["idp_certificates"]) == [pem.strip()]
        assert (
            dbs["sp_decryption_key_ref"] == "secret/sp-key" and dbs["require_encrypted_assertions"]
        )

        # rotate: only the fields that belong to the protocol, a bounded validity, blanks keep the current value
        rot = f"/federation/integrations/{cid}:rotate"
        assert (
            http.post(
                rot, headers=adm, json={"metadata_expires_at": soon(), "idp_certificates": [pem]}
            ).status_code
            == 422
        )
        assert (
            http.post(
                rot, headers=adm, json={"metadata_expires_at": soon(), "jwks": {"keys": []}}
            ).status_code
            == 422
        )
        assert (
            http.post(rot, headers=adm, json={"metadata_expires_at": soon(900)}).status_code == 422
        )
        r = http.post(
            rot,
            headers=adm,
            json={"metadata_expires_at": soon(60), "client_secret_ref": "  ", "jwks": JWKS},
        )
        assert r.status_code == 200 and r.json()["config_revision"] == 2
        assert row(cid)["client_secret_ref"] == "secret/acme" and row(cid)["config_revision"] == 2
        sr = f"/federation/integrations/{s['connection_id']}:rotate"
        assert (
            http.post(
                sr, headers=adm, json={"metadata_expires_at": soon(), "jwks": JWKS}
            ).status_code
            == 422
        )
        assert (
            http.post(
                sr, headers=adm, json={"metadata_expires_at": soon(), "idp_certificates": []}
            ).status_code
            == 422
        )
        r = http.post(
            sr, headers=adm, json={"metadata_expires_at": soon(), "idp_certificates": [cert_pem()]}
        )
        assert r.status_code == 200 and r.json()["pinned_certificates"] == 1

        # disable: once, then idempotent; a disabled provider cannot be rotated
        off = f"/federation/integrations/{cid}:disable"
        r = http.post(off, headers=adm)
        assert (
            r.status_code == 200
            and r.json()["status"] == "QUARANTINED"
            and r.json()["config_revision"] == 3
        )
        again = http.post(off, headers=adm)
        assert again.status_code == 200 and again.json()["config_revision"] == 3
        assert row(cid)["status"] == "QUARANTINED" and row(cid)["config_revision"] == 3
        assert http.post(rot, headers=adm, json={"metadata_expires_at": soon()}).status_code == 409
        assert (
            http.post(f"/federation/integrations/{uid()}:disable", headers=adm).status_code == 404
        )

        # trust anchors: domains are normalised, duplicates refused, keys counted
        TA = "https://anchor.example.org"
        anchor = {
            "entity_id": TA,
            "jwks": JWKS,
            "allowed_domains": [" Fed.Example ", "fed.example", "b.example"],
        }
        r = http.post("/federation/trust-anchors", headers=adm, json=anchor)
        assert r.status_code == 201, r.text
        aid = r.json()["anchor_id"]
        dup = http.post("/federation/trust-anchors", headers=adm, json=anchor)
        assert dup.status_code == 409 and dup.json()["error"]["code"] == "TRUST_ANCHOR_EXISTS"
        for body in (
            {**anchor, "entity_id": "https://other.example", "allowed_domains": ["not a domain"]},
            {**anchor, "entity_id": "https://other.example", "jwks": {"keys": []}},
            {**anchor, "entity_id": "https://has space.example"},
            {**anchor, "entity_id": "http://plain.example"},
        ):
            assert http.post("/federation/trust-anchors", headers=adm, json=body).status_code in (
                409,
                422,
            )
        (a,) = http.get("/federation/trust-anchors", headers=adm).json()["items"]
        assert a["entity_id"] == TA and a["allowed_domains"] == ["b.example", "fed.example"]
        assert a["pinned_keys"] == 1 and a["status"] == "ACTIVE" and a["revision"] == 1

        # a federation-resolved provider through that anchor
        fed = {
            "protocol": "OIDC",
            "display_name": "Via federation",
            "issuer": "https://op.fed.example",
            "client_id": "rp",
            "client_auth": "private_key_jwt",
            "trust_anchor_id": aid,
            "federation_entity_id": "https://op.fed.example",
            "metadata_expires_at": soon(),
        }
        r = http.post("/federation/integrations", headers=adm, json={**fed, "jwks": JWKS})
        assert r.status_code == 422 and "no pinned endpoints" in r.text
        r = http.post("/federation/integrations", headers=adm, json=fed)
        assert r.status_code == 201, r.text
        assert (
            r.json()["trust_anchor_id"] == aid
            and r.json()["federation_entity_id"] == fed["federation_entity_id"]
        )
        assert str(row(r.json()["connection_id"])["trust_anchor_id"]) == aid

        r = http.post(f"/federation/trust-anchors/{aid}:disable", headers=adm)
        assert r.status_code == 200 and r.json()["status"] == "QUARANTINED"
        r = http.post(
            "/federation/integrations",
            headers=adm,
            json={
                **fed,
                "issuer": "https://op2.fed.example",
                "federation_entity_id": "https://op2.fed.example",
            },
        )
        assert r.status_code == 422 and "active trust anchor" in r.text
        assert (
            http.post(f"/federation/trust-anchors/{uid()}:disable", headers=adm).status_code == 404
        )

        # anchors: disabling again changes nothing; enabling restores it once and is then a no-op
        assert http.post(f"/federation/trust-anchors/{aid}:disable", headers=adm).status_code == 200
        assert arow(aid)["revision"] == 2 and arow(aid)["status"] == "QUARANTINED"
        r = http.post(f"/federation/trust-anchors/{aid}:enable", headers=adm, json={})
        assert r.status_code == 200 and r.json()["status"] == "ACTIVE"
        assert http.post(f"/federation/trust-anchors/{aid}:enable", headers=adm).status_code == 200
        assert arow(aid)["revision"] == 3 and arow(aid)["status"] == "ACTIVE"
        assert (
            http.post(f"/federation/trust-anchors/{uid()}:enable", headers=adm).status_code == 404
        )
        r = http.post(
            "/federation/integrations",
            headers=adm,
            json={
                **fed,
                "issuer": "https://op2.fed.example",
                "federation_entity_id": "https://op2.fed.example",
            },
        )
        assert r.status_code == 201, r.text

        # update: only non-trust settings; a change bumps the revision and lands in its column, no change does not
        sid_ = s["connection_id"]
        up = f"/federation/integrations/{sid_}"
        r = http.patch(up, headers=adm, json={"display_name": "  Acme ADFS 2 "})
        assert r.status_code == 200 and r.json()["display_name"] == "Acme ADFS 2"
        assert r.json()["config_revision"] == 3  # created at 1, rotated to 2
        assert row(sid_)["display_name"] == "Acme ADFS 2" and row(sid_)["config_revision"] == 3
        same = http.patch(up, headers=adm, json={"display_name": "Acme ADFS 2", "trust_amr": True})
        assert same.status_code == 200 and same.json()["config_revision"] == 3
        assert http.patch(up, headers=adm, json={}).json()["config_revision"] == 3
        r = http.patch(
            up,
            headers=adm,
            json={
                "link_policy": "JIT",
                "trust_amr": False,
                "acr_levels": {"urn:x": 7},
                "algorithms": ["PS256"],
                "sp_entity_id": "https://sp.acme.example/new",
                "require_encrypted_assertions": False,
            },
        )
        assert r.status_code == 200, r.text
        dbu = row(sid_)
        assert dbu["link_policy"] == "JIT" and dbu["trust_amr"] is False
        assert dbu["acr_levels"] == {"urn:x": 2} and list(dbu["algorithms"]) == ["PS256"]
        assert dbu["sp_entity_id"] == "https://sp.acme.example/new" and dbu["config_revision"] == 4
        assert dbu["require_encrypted_assertions"] is False and dbu["sso_url"] == saml["sso_url"]
        assert r.json()["acs_url"] == ISS + "/federation/saml/acs"
        for bad_body in (
            {"scopes": ["email"]},  # OIDC only
            {"issuer": "https://evil.example"},  # trust fields are not updatable
            {"jwks": JWKS},
            {"idp_certificates": [pem]},
            {"display_name": "   "},
            {"acr_levels": {"a": -1}},
            {"algorithms": []},
            {"link_policy": "ANYONE"},
        ):
            assert http.patch(up, headers=adm, json=bad_body).status_code == 422, bad_body
        assert row(sid_)["issuer"] == saml["issuer"] and row(sid_)["config_revision"] == 4

        oid = http.post(
            "/federation/integrations", headers=adm, json={**oidc, "issuer": IDP + "/two"}
        ).json()["connection_id"]
        r = http.patch(
            f"/federation/integrations/{oid}",
            headers=adm,
            json={"scopes": ["groups"], "algorithms": ["ES256"], "display_name": "Two"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["scopes"] == ["openid", "groups"] and r.json()["algorithms"] == ["ES256"]
        assert list(row(oid)["scopes"]) == ["openid", "groups"] and row(oid)["config_revision"] == 2
        for bad_body in (
            {"sp_entity_id": "https://x.example"},
            {"require_encrypted_assertions": True},
        ):
            assert (
                http.patch(
                    f"/federation/integrations/{oid}", headers=adm, json=bad_body
                ).status_code
                == 422
            )
        # encryption cannot be required without a key reference
        noenc = http.post(
            "/federation/integrations",
            headers=adm,
            json={
                **saml,
                "issuer": "https://idp2.acme.example/saml",
                "require_encrypted_assertions": False,
                "sp_decryption_key_ref": None,
            },
        ).json()["connection_id"]
        r = http.patch(
            f"/federation/integrations/{noenc}",
            headers=adm,
            json={"require_encrypted_assertions": True},
        )
        assert r.status_code == 422 and "sp_decryption_key_ref" in r.text
        assert (
            http.patch(f"/federation/integrations/{uid()}", headers=adm, json={}).status_code == 404
        )

        # a disabled provider cannot be updated, enabling restores it once, then it is a no-op
        assert (
            http.patch(
                f"/federation/integrations/{cid}", headers=adm, json={"trust_amr": True}
            ).status_code
            == 409
        )
        on = f"/federation/integrations/{cid}:enable"
        r = http.post(on, headers=adm, json={})
        assert r.status_code == 200 and r.json()["status"] == "ACTIVE"
        assert r.json()["config_revision"] == 4
        assert http.post(on, headers=adm).json()["config_revision"] == 4
        assert row(cid)["status"] == "ACTIVE" and row(cid)["config_revision"] == 4
        assert (
            http.patch(
                f"/federation/integrations/{cid}", headers=adm, json={"display_name": "Back"}
            ).status_code
            == 200
        )
        assert http.post(f"/federation/integrations/{uid()}:enable", headers=adm).status_code == 404
