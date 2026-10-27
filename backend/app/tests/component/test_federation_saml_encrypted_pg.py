"""SAML encrypted assertions and governed IdP-initiated SSO (ZID-AUTH-001 §10; AUTH-W5).

A fake IdP signs and then encrypts assertions to the SP's RSA key (XML Encryption 1.1, AES-GCM
with RSA-OAEP). Covered: plaintext refused once encryption is required, signed-then-encrypted and
signed-Response-around-ciphertext both accepted, an encrypted but unsigned assertion refused,
CBC / RSA 1.5 / tampered ciphertext / wrong key refused alike. Then IdP-initiated SSO: refused by
default, enabling needs a second administrator's approval bound to the exact targets, and once
enabled only the connection's own ACS accepts a fresh unsolicited assertion with an approved
RelayState target; replay, stale and solicited-looking assertions are refused.
"""

from __future__ import annotations

import asyncio
import base64
import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from admin_fixture import make_admin
from conftest import ctx, engine
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi.testclient import TestClient
from lxml import etree
from sqlalchemy import text
from test_federation_saml_pg import (
    ACS,
    AUD,
    FORM,
    IDP,
    ISS,
    NS,
    PPT,
    SP,
    assertion,
    keypair,
    response,
    sign,
    ts,
)
from zid_crypto import KeyRing, LocalSigner
from zid_db import tenant_transaction
from zid_platform.main import create_app
from zid_platform.modules.authentication.ports import create_external_link
from zid_platform.modules.federation.api.federation import ADMIN_ACTION, idp_initiated_change
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
from zid_platform.protected import transaction_context
from zid_platform.settings import Settings

XENC = "http://www.w3.org/2001/04/xmlenc#"
XENC11 = "http://www.w3.org/2009/xmlenc11#"
DS = "http://www.w3.org/2000/09/xmldsig#"
LANDING = "https://portal.acme.example/home"
KEY_REF = "saml-sp-key"


def uid() -> str:
    return str(uuid.uuid4())


def encrypt(
    plain: etree._Element,
    sp_public: rsa.RSAPublicKey,
    *,
    content: str = "aes256-gcm",
    transport: str = "rsa-oaep-mgf1p",
    tamper: bool = False,
) -> etree._Element:
    """saml:EncryptedAssertion around `plain`, as an IdP would build it."""
    data = etree.tostring(plain)
    if content == "aes256-cbc":
        cek, iv = os.urandom(32), os.urandom(16)
        pad = 16 - len(data) % 16
        enc = Cipher(algorithms.AES(cek), modes.CBC(iv)).encryptor()
        blob = iv + enc.update(data + bytes([pad]) * pad) + enc.finalize()
        content_uri = XENC + "aes256-cbc"
    else:
        cek, iv = os.urandom(32), os.urandom(12)
        blob = iv + AESGCM(cek).encrypt(iv, data, None)
        content_uri = XENC11 + content
    if tamper:
        blob = blob[:-1] + bytes([blob[-1] ^ 1])
    if transport == "rsa-1_5":
        wrapped = sp_public.encrypt(cek, padding.PKCS1v15())
    else:
        wrapped = sp_public.encrypt(
            cek, padding.OAEP(mgf=padding.MGF1(hashes.SHA1()), algorithm=hashes.SHA1(), label=None)
        )
    ea = etree.Element(f"{{{NS['saml']}}}EncryptedAssertion", nsmap={**NS, "xenc": XENC})
    ed = etree.SubElement(ea, f"{{{XENC}}}EncryptedData", Type=XENC + "Element")
    etree.SubElement(ed, f"{{{XENC}}}EncryptionMethod", Algorithm=content_uri)
    ki = etree.SubElement(ed, f"{{{DS}}}KeyInfo", nsmap={"ds": DS})
    ek = etree.SubElement(ki, f"{{{XENC}}}EncryptedKey")
    etree.SubElement(ek, f"{{{XENC}}}EncryptionMethod", Algorithm=XENC + transport)
    cd = etree.SubElement(etree.SubElement(ek, f"{{{XENC}}}CipherData"), f"{{{XENC}}}CipherValue")
    cd.text = base64.b64encode(wrapped).decode()
    cv = etree.SubElement(etree.SubElement(ed, f"{{{XENC}}}CipherData"), f"{{{XENC}}}CipherValue")
    cv.text = base64.b64encode(blob).decode()
    return ea


def element(b64: str) -> etree._Element:
    return etree.fromstring(base64.b64decode(b64))


def encode(el: etree._Element) -> str:
    return base64.b64encode(etree.tostring(el)).decode()


def test_encrypted_assertions_and_governed_idp_initiated_sso(
    rt_url: str, owner_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SEC-27 (federation changes): enabling an IdP-initiated path is refused without a second administrator's approval."""
    tenant, alice, admin, approver = uid(), uid(), uid(), uid()
    web = f"web-{uid()}"
    key, cert = keypair()
    sp_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    other_sp = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setenv(
        "ZID_SECRET__" + KEY_REF.upper().replace("-", "_"),
        sp_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode(),
    )
    ring = KeyRing(LocalSigner("issuer-1"))

    async def session_for(c: Any, p: str) -> str:
        sid, now = uid(), datetime.now(UTC)
        principal, _ = await load_principal(c, p)
        await create_session(
            c,
            Session(
                sid,
                tenant,
                p,
                now - timedelta(seconds=10),
                "native:webauthn",
                2,
                principal.security_revision,
                1,
                SessionStatus.ACTIVE,
                now + timedelta(hours=1),
                now,
            ),
        )
        return sid

    async def seed() -> tuple[str, str]:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            for p in (alice, admin, approver):
                await create_principal(
                    c, Principal(p, tenant, PrincipalType.HUMAN, PrincipalStatus.ACTIVE, "uk-1")
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
            await create_external_link(
                c,
                tenant_id=tenant,
                principal_id=alice,
                issuer=IDP,
                subject="alice-nid",
                connection_id=None,
                link_assurance="ENTERPRISE_PROVISIONED",
                now=datetime.now(UTC),
            )
            await make_admin(
                c, tenant, [admin, approver], [ADMIN_ACTION], ["federation_connection:*"]
            )
            return await session_for(c, admin), await session_for(c, approver)

    admin_sid, approver_sid = asyncio.run(seed())
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

    def bearer(sub: str, sid: str) -> dict[str, str]:
        tok = issue_access_token(
            ring,
            issuer=ISS,
            audience=AUD,
            subject=sub,
            tenant_id=tenant,
            client_id=web,
            now=datetime.now(UTC),
            session_id=sid,
        )
        return {"Authorization": f"Bearer {tok}"}

    adm, appr = bearer(admin, admin_sid), bearer(approver, approver_sid)

    with TestClient(app, base_url=ISS, follow_redirects=False) as http:
        r = http.post(
            "/federation/integrations",
            headers=adm,
            json={
                "protocol": "SAML",
                "display_name": "Acme Entra",
                "issuer": IDP,
                "sso_url": "https://saml.acme.example/sso",
                "idp_certificates": [cert],
                "sp_entity_id": SP,
                "sp_decryption_key_ref": KEY_REF,
                "require_encrypted_assertions": True,
                "acr_levels": {PPT: 1},
                "metadata_expires_at": (datetime.now(UTC) + timedelta(days=30)).isoformat(),
            },
        )
        assert r.status_code == 201, r.text
        assert r.json()["require_encrypted_assertions"] is True
        conn_id = r.json()["connection_id"]
        own_acs = f"{ACS}/{tenant}.{conn_id}"

        def start() -> tuple[str, str]:
            r = http.get(f"/federation/connections/{conn_id}/login", params={"client_id": web})
            assert r.status_code == 303, r.text
            q = {k: v[0] for k, v in parse_qs(urlsplit(r.headers["location"]).query).items()}
            import zlib

            req = etree.fromstring(zlib.decompress(base64.b64decode(q["SAMLRequest"]), -15))
            return req.get("ID"), q["RelayState"]

        def acs(saml_response: str, relay: str | None, url: str = ACS) -> Any:
            data = {"SAMLResponse": saml_response} | ({"RelayState": relay} if relay else {})
            return http.post(url, data=data, headers=FORM)

        def code(r: Any) -> str:
            return r.json()["error"]["code"]

        pub = sp_key.public_key()

        # signed, then encrypted to the SP key
        rid, relay = start()
        r = acs(response(rid, encrypt(sign(assertion(rid, "alice-nid"), key, cert), pub)), relay)
        assert r.status_code == 200, r.text
        assert r.json()["principal_id"] == alice

        # a signed Response around the ciphertext (assertion itself unsigned) is also accepted
        rid, relay = start()
        envelope = element(response(rid, encrypt(assertion(rid, "alice-nid"), pub)))
        r = acs(encode(sign(envelope, key, cert)), relay)
        assert r.status_code == 200, r.text

        cases = [
            (
                lambda rid: response(rid, sign(assertion(rid, "alice-nid"), key, cert)),
                "SAML_ASSERTION_NOT_ENCRYPTED",
            ),
            (
                lambda rid: response(rid, encrypt(assertion(rid, "alice-nid"), pub)),
                "SAML_SIGNATURE_INVALID",
            ),
            (
                lambda rid: response(
                    rid,
                    encrypt(
                        sign(assertion(rid, "alice-nid"), key, cert), pub, content="aes256-cbc"
                    ),
                ),
                "SAML_DECRYPTION_FAILED",
            ),
            (
                lambda rid: response(
                    rid,
                    encrypt(sign(assertion(rid, "alice-nid"), key, cert), pub, transport="rsa-1_5"),
                ),
                "SAML_DECRYPTION_FAILED",
            ),
            (
                lambda rid: response(
                    rid, encrypt(sign(assertion(rid, "alice-nid"), key, cert), pub, tamper=True)
                ),
                "SAML_DECRYPTION_FAILED",
            ),
            (
                lambda rid: response(
                    rid,
                    encrypt(sign(assertion(rid, "alice-nid"), key, cert), other_sp.public_key()),
                ),
                "SAML_DECRYPTION_FAILED",
            ),
        ]
        for build, expected in cases:
            rid, relay = start()
            r = acs(build(rid), relay)
            assert r.status_code == 401 and code(r) == expected, (expected, r.text)

        # ---- IdP-initiated SSO
        def unsolicited(**kw: Any) -> str:
            a = assertion("_none", "alice-nid", recipient=own_acs, **kw)
            a.find(".//saml:SubjectConfirmationData", NS).attrib.pop("InResponseTo")
            if kw.get("stale"):
                a.find("saml:AuthnStatement", NS).set(
                    "AuthnInstant", ts(datetime.now(UTC) - timedelta(minutes=30))
                )
            env = element(response("_none", encrypt(sign(a, key, cert), pub), destination=own_acs))
            env.attrib.pop("InResponseTo")
            return encode(env)

        # off by default, both at the shared ACS and the connection's own
        assert code(acs(unsolicited(), None)) == "SAML_UNSOLICITED"
        assert code(acs(unsolicited(), None, own_acs)) == "SAML_UNSOLICITED"

        path = f"/federation/integrations/{conn_id}:idp-initiated"
        change = idp_initiated_change(True, [LANDING])
        r = http.post(path, headers=adm, json={"enabled": True, "targets": [LANDING]})
        assert r.status_code == 403 and code(r) == "APPROVAL_REQUIRED"
        r = http.post(path, headers=adm, json={"enabled": True, "targets": ["http://x/y"]})
        assert r.status_code == 422

        async def grant_of(p: str) -> str:
            async with engine(owner_url) as eng, eng.begin() as c:
                return str(
                    (
                        await c.execute(
                            text(
                                "SELECT authority_grant_id FROM authz.authority_grant "
                                "WHERE grantee_principal_id = :p"
                            ),
                            {"p": uuid.UUID(p)},
                        )
                    ).scalar_one()
                )

        resource = f"federation_connection:{conn_id}"
        tx = transaction_context(ADMIN_ACTION, resource, change)
        r = http.post(
            "/v1/approval-requests",
            headers=adm,
            json={"subject_ref": resource, "transaction_context": tx, "roles": [ADMIN_ACTION]},
        )
        assert r.status_code == 201, r.text
        approval = r.json()["approval_request_id"]
        r = http.post(
            f"/v1/approval-requests/{approval}/decisions",
            headers=appr,
            json={
                "verdict": "APPROVE",
                "authority_grant_id": asyncio.run(grant_of(approver)),
                "transaction_context": tx,
            },
        )
        assert r.status_code == 201 and r.json()["state"] == "APPROVED", r.text

        # the approval binds the exact targets: a different set is not covered by it
        r = http.post(
            path,
            headers=adm,
            json={
                "enabled": True,
                "targets": [LANDING, "https://evil.example/"],
                "approval_request_id": approval,
            },
        )
        assert r.status_code == 403 and code(r) == "APPROVAL_REQUIRED"
        r = http.post(
            path,
            headers=adm,
            json={"enabled": True, "targets": [LANDING], "approval_request_id": approval},
        )
        assert r.status_code == 200, r.text
        assert r.json()["allow_idp_initiated"] and r.json()["acs_url"] == own_acs

        # the shared ACS still refuses unsolicited responses
        assert code(acs(unsolicited(), None)) == "SAML_UNSOLICITED"
        # the connection's own ACS: fresh, no InResponseTo, approved landing target only
        r = acs(unsolicited(), LANDING, own_acs)
        assert r.status_code == 303 and r.headers["location"] == LANDING, r.text
        r = acs(unsolicited(), None, own_acs)
        assert r.status_code == 200 and r.json()["principal_id"] == alice
        assert code(acs(unsolicited(), "https://evil.example/", own_acs)) == (
            "SAML_RELAY_STATE_REJECTED"
        )
        assert code(acs(unsolicited(stale=True), None, own_acs)) == "SAML_ASSERTION_STALE"
        replayed = unsolicited(assertion_id="_idp-init-fixed")
        assert acs(replayed, None, own_acs).status_code == 200
        assert code(acs(replayed, None, own_acs)) == "SAML_ASSERTION_REPLAYED"
        solicited_looking = encode(
            element(
                response(
                    "_x",
                    encrypt(sign(assertion("_x", "alice-nid", recipient=own_acs), key, cert), pub),
                    destination=own_acs,
                )
            )
        )
        assert code(acs(solicited_looking, None, own_acs)) == "SAML_IN_RESPONSE_TO_MISMATCH"
        # an assertion addressed to the shared ACS is not valid at the connection's own
        rid, _ = start()
        plain_target = encode(
            element(response(rid, encrypt(sign(assertion(rid, "alice-nid"), key, cert), pub)))
        )
        assert code(acs(plain_target, None, own_acs)) in (
            "SAML_DESTINATION_MISMATCH",
            "SAML_IN_RESPONSE_TO_MISMATCH",
        )

        # switching it off needs no approval and takes effect at once
        r = http.post(path, headers=adm, json={"enabled": False})
        assert r.status_code == 200 and not r.json()["allow_idp_initiated"]
        assert code(acs(unsolicited(), None, own_acs)) == "SAML_UNSOLICITED"
