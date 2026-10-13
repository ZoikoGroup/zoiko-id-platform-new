"""OID4VP under the HAIP profile (ZID-VC-001 §11; OpenID4VC HAIP 1.0).

x509_hash client identifier: the request object carries the verifier certificate in x5c and the
client_id is the hash of that leaf, so the wallet can bind both. direct_post.jwt: the wallet
encrypts its response (ECDH-ES, A128GCM/A256GCM) to a key that exists only for this presentation;
plaintext responses, other keys, other transactions' states, other algorithms and replays after
completion are refused, and the private key is gone once the presentation completes.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

from admin_fixture import make_admin
from conftest import ctx, engine
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from fastapi.testclient import TestClient
from joserfc import jwe
from joserfc.jwk import ECKey, RSAKey
from softcred import Mdoc
from sqlalchemy import text
from zid_crypto import KeyRing, LocalSigner, b64url, b64url_decode, peek_header
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
FORM = {"Content-Type": "application/x-www-form-urlencoded"}


def uid() -> str:
    return str(uuid.uuid4())


def test_haip_x509_hash_and_encrypted_responses(rt_url: str, owner_url: str) -> None:
    tenant, admin = uid(), uid()
    ring = KeyRing(LocalSigner("verifier-1"))
    mdl = Mdoc()

    async def seed() -> str:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            await create_principal(
                c, Principal(admin, tenant, PrincipalType.HUMAN, PrincipalStatus.ACTIVE, "uk-1")
            )
            await make_admin(
                c,
                tenant,
                admin,
                ["credential.trust.manage"],
                ["credential_configuration:*", "credential_issuer:*", "rp_registration:*"],
            )
            sid, now = uid(), datetime.now(UTC)
            pr, _ = await load_principal(c, admin)
            await create_session(
                c,
                Session(
                    sid,
                    tenant,
                    admin,
                    now - timedelta(seconds=5),
                    "native:webauthn",
                    2,
                    pr.security_revision,
                    1,
                    SessionStatus.ACTIVE,
                    now + timedelta(hours=1),
                    now,
                ),
            )
            return sid

    admin_sid = asyncio.run(seed())
    app = create_app(
        Settings(
            database_url=rt_url,
            token_issuer=ISS,
            token_audience=AUD,
            public_base_url=AUD,
            environment="test",
            verifier_client_id_prefix="x509_hash",
        ),
        keyring=ring,
    )

    def bearer(client: str, scope: str = "", sid: str | None = None) -> dict[str, str]:
        tok = issue_access_token(
            ring,
            issuer=ISS,
            audience=AUD,
            subject=admin,
            tenant_id=tenant,
            client_id=client,
            now=datetime.now(UTC),
            session_id=sid,
            scope=frozenset(scope.split()),
        )
        return {"Authorization": f"Bearer {tok}"}

    adm, rp = bearer("console", sid=admin_sid), bearer("rp-bank", "presentation:request")
    until = (datetime.now(UTC) + timedelta(days=30)).isoformat()

    with TestClient(app, base_url=ISS) as http:

        def admin_post(path: str, body: dict[str, Any]) -> Any:
            r = http.post(path, headers=adm, json=body)
            assert r.status_code in (200, 201), r.text
            return r.json()

        cfg = admin_post(
            "/credential-configurations",
            {
                "credential_type": mdl.doc_type,
                "format": "MDOC",
                "required_claims": ["age_over_18"],
                "status_method": "NONE",
                "status_mandatory": False,
            },
        )["configuration_id"]
        admin_post(f"/credential-configurations/{cfg}:activate", {})
        admin_post(
            "/credential-issuer-trust",
            {
                "issuer_identifier": "https://mdl.dvla.example",
                "credential_types": [mdl.doc_type],
                "trust_source": "PINNED",
                "iaca_certificates": [mdl.iaca_pem()],
                "valid_to": until,
            },
        )
        admin_post(
            "/relying-party-registrations",
            {
                "client_id": "rp-bank",
                "purpose": "age",
                "allowed_attributes": ["age_over_18"],
                "valid_to": until,
            },
        )

        def request(mode: str = "direct_post.jwt") -> tuple[str, dict[str, Any], dict[str, Any]]:
            r = http.post(
                "/presentations",
                headers=rp,
                json={
                    "configuration_id": cfg,
                    "purpose": "age",
                    "claims": ["age_over_18"],
                    "jurisdiction": "GB",
                    "response_mode": mode,
                },
            )
            assert r.status_code == 201, r.text
            ro = http.get(urlsplit(r.json()["request_uri"]).path)
            header = peek_header(ro.text)
            # as a wallet does: the key comes from the x5c leaf the client_id hashes
            leaf = x509.load_der_x509_certificate(base64.b64decode(header["x5c"][0]))
            assert header["typ"] == "oauth-authz-req+jwt" and header["alg"] == "ES256"
            h, body, sig = ro.text.split(".")
            raw = b64url_decode(sig)
            der = encode_dss_signature(
                int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big")
            )
            leaf.public_key().verify(der, f"{h}.{body}".encode(), ec.ECDSA(hashes.SHA256()))  # type: ignore[call-arg]
            return r.json()["presentation_id"], header, json.loads(b64url_decode(body))

        def vp(req: dict[str, Any]) -> dict[str, Any]:
            device_response = mdl.device_response(
                {"age_over_18": True},
                client_id=req["client_id"],
                nonce=req["nonce"],
                response_uri=req["response_uri"],
            )
            return {"cred1": [device_response]}

        def encrypt(req: dict[str, Any], payload: dict[str, Any], **kw: Any) -> str:
            jwk = kw.pop("jwk", None) or req["client_metadata"]["jwks"]["keys"][0]
            header = {"alg": "ECDH-ES", "enc": "A256GCM", "kid": jwk.get("kid"), **kw}
            key = jwk if isinstance(jwk, ECKey | RSAKey) else ECKey.import_key(jwk)
            return jwe.encrypt_compact(header, json.dumps(payload).encode(), key)

        def post(req: dict[str, Any], data: dict[str, str]) -> Any:
            return http.post(urlsplit(req["response_uri"]).path, headers=FORM, data=data)

        def error(r: Any) -> str:
            return r.json()["error_description"]

        # x509_hash: client_id is the hash of the x5c leaf that signs the request object
        pid, header, req = request()
        leaf = base64.b64decode(header["x5c"][0])
        assert req["client_id"] == "x509_hash:" + b64url(hashlib.sha256(leaf).digest())
        assert req["iss"] == req["client_id"]
        cert_key = x509.load_der_x509_certificate(leaf).public_key()
        assert ECKey.import_key(cert_key).as_dict()["x"] == ring.active.public_jwk()["x"]  # type: ignore[arg-type]
        assert req["response_mode"] == "direct_post.jwt"
        keys = req["client_metadata"]["jwks"]["keys"]
        assert len(keys) == 1 and keys[0]["kid"] == pid and keys[0]["alg"] == "ECDH-ES"
        assert "d" not in keys[0]

        # plaintext is refused for an encrypted transaction (no form-field way around it)
        plain = {"state": req["state"], "vp_token": json.dumps(vp(req))}
        assert error(post(req, plain)) == "RESPONSE_ENCRYPTION_REQUIRED"
        assert error(post(req, {**plain, "\x00decrypted": "1"})) == "RESPONSE_ENCRYPTION_REQUIRED"
        # wrong key, wrong algorithms
        other = ECKey.generate_key("P-256", private=True)
        wrong = encrypt(req, {"state": req["state"], "vp_token": vp(req)}, jwk=other, kid=pid)
        assert error(post(req, {"response": wrong})) == "RESPONSE_DECRYPTION_FAILED"
        cbc = encrypt(req, {"state": req["state"], "vp_token": vp(req)}, enc="A128CBC-HS256")
        assert error(post(req, {"response": cbc})) == "RESPONSE_DECRYPTION_FAILED"
        rsa = RSAKey.generate_key(2048, private=True)
        oaep = jwe.encrypt_compact(
            {"alg": "RSA-OAEP-256", "enc": "A256GCM", "kid": pid},
            b"{}",
            rsa,
            algorithms=["RSA-OAEP-256", "A256GCM"],
        )
        assert error(post(req, {"response": oaep})) == "RESPONSE_DECRYPTION_FAILED"
        # another transaction's state inside this transaction's encryption
        _, _, req2 = request()
        crossed = encrypt(req, {"state": req2["state"], "vp_token": vp(req2)})
        assert error(post(req, {"response": crossed})) == "PRESENTATION_STATE_INVALID"

        # the real thing
        good = encrypt(req, {"state": req["state"], "vp_token": vp(req)})
        r = post(req, {"response": good})
        assert r.status_code == 200, r.text
        out = http.get(f"/presentations/{pid}", headers=rp).json()
        assert out["status"] == "VERIFIED" and out["claims"] == {"age_over_18": True}
        assert error(post(req, {"response": good})) == "PRESENTATION_STATE_INVALID"  # replay

        async def response_key(p: str) -> Any:
            async with engine(owner_url) as eng, eng.begin() as c:
                return (
                    await c.execute(
                        text(
                            "SELECT response_key FROM cred.presentation_record "
                            "WHERE presentation_id = :p"
                        ),
                        {"p": uuid.UUID(p)},
                    )
                ).scalar_one()

        assert asyncio.run(response_key(pid)) is None  # wiped with completion

        # A128GCM is fine too; plain direct_post still works where it was asked for
        _, _, req3 = request()
        good3 = encrypt(req3, {"state": req3["state"], "vp_token": vp(req3)}, enc="A128GCM")
        assert post(req3, {"response": good3}).status_code == 200
        _, _, req4 = request("direct_post")
        assert "jwks" not in req4["client_metadata"]
        r = post(req4, {"state": req4["state"], "vp_token": json.dumps(vp(req4))})
        assert r.status_code == 200, r.text
