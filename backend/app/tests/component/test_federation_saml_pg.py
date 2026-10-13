"""SAML 2.0 SP adapter end to end (ZID-AUTH-001 §10; AUTH-03, AUTH-10, AUTH-15; AUTH-W5).

A fake IdP signs real assertions (XML-DSig, RSA-SHA256, signxml). Covered: happy path through an
existing issuer+NameID link, signature wrapping (an unsigned forged assertion beside the signed
one is ignored), XXE/DTD, unsigned and SHA-1 signatures, wrong signer, audience/recipient/
InResponseTo mismatches, expiry, transient NameIDs, assertion replay, IdP-initiated SSO and a
certificate rollover.
"""

from __future__ import annotations

import asyncio
import base64
import uuid
import zlib
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlsplit

from admin_fixture import make_admin
from conftest import ctx, engine
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from fastapi.testclient import TestClient
from lxml import etree
from signxml.algorithms import DigestAlgorithm, SignatureMethod
from signxml.signer import XMLSigner
from zid_crypto import KeyRing, LocalSigner
from zid_db import tenant_transaction
from zid_platform.main import create_app
from zid_platform.modules.authentication.ports import create_external_link
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
IDP = "https://saml.acme.example/idp"
SP = "https://id.zoiko.test/saml/sp"
ACS = ISS + "/federation/saml/acs"
NS = {
    "samlp": "urn:oasis:names:tc:SAML:2.0:protocol",
    "saml": "urn:oasis:names:tc:SAML:2.0:assertion",
}
PERSISTENT = "urn:oasis:names:tc:SAML:2.0:nameid-format:persistent"
PPT = "urn:oasis:names:tc:SAML:2.0:ac:classes:PasswordProtectedTransport"
FORM = {"Content-Type": "application/x-www-form-urlencoded"}


def uid() -> str:
    return str(uuid.uuid4())


def keypair() -> tuple[bytes, str]:
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
    pem_key = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    return pem_key, cert.public_bytes(serialization.Encoding.PEM).decode()


def ts(t: datetime) -> str:
    return t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def assertion(request_id: str, name_id: str, **kw: Any) -> etree._Element:
    now = datetime.now(UTC)
    a = etree.Element(
        f"{{{NS['saml']}}}Assertion",
        nsmap=NS,
        ID=kw.get("assertion_id", "_" + uuid.uuid4().hex),
        Version="2.0",
        IssueInstant=ts(now),
    )
    etree.SubElement(a, f"{{{NS['saml']}}}Issuer").text = kw.get("issuer", IDP)
    subject = etree.SubElement(a, f"{{{NS['saml']}}}Subject")
    nid = etree.SubElement(subject, f"{{{NS['saml']}}}NameID", Format=kw.get("fmt", PERSISTENT))
    nid.text = name_id
    sc = etree.SubElement(
        subject,
        f"{{{NS['saml']}}}SubjectConfirmation",
        Method="urn:oasis:names:tc:SAML:2.0:cm:bearer",
    )
    etree.SubElement(
        sc,
        f"{{{NS['saml']}}}SubjectConfirmationData",
        Recipient=kw.get("recipient", ACS),
        InResponseTo=kw.get("irt", request_id),
        NotOnOrAfter=ts(now + kw.get("ttl", timedelta(minutes=5))),
    )
    cond = etree.SubElement(
        a,
        f"{{{NS['saml']}}}Conditions",
        NotBefore=ts(now - timedelta(seconds=30)),
        NotOnOrAfter=ts(now + kw.get("ttl", timedelta(minutes=5))),
    )
    ar = etree.SubElement(cond, f"{{{NS['saml']}}}AudienceRestriction")
    etree.SubElement(ar, f"{{{NS['saml']}}}Audience").text = kw.get("audience", SP)
    st = etree.SubElement(
        a, f"{{{NS['saml']}}}AuthnStatement", AuthnInstant=ts(now - timedelta(seconds=5))
    )
    ctxe = etree.SubElement(st, f"{{{NS['saml']}}}AuthnContext")
    etree.SubElement(ctxe, f"{{{NS['saml']}}}AuthnContextClassRef").text = PPT
    return a


def sign(a: etree._Element, key: bytes, cert: str, alg: str = "sha256") -> etree._Element:
    signed = XMLSigner(
        signature_algorithm=SignatureMethod.RSA_SHA256, digest_algorithm=DigestAlgorithm.SHA256
    ).sign(a, key=key, cert=cert, reference_uri=a.get("ID"))
    if alg == "sha1":  # what a legacy IdP would send: the policy must refuse it outright
        ds = "{http://www.w3.org/2000/09/xmldsig#}"
        signed.find(f".//{ds}SignatureMethod").set(
            "Algorithm", "http://www.w3.org/2000/09/xmldsig#rsa-sha1"
        )
        signed.find(f".//{ds}DigestMethod").set(
            "Algorithm", "http://www.w3.org/2000/09/xmldsig#sha1"
        )
    return signed


def response(request_id: str, *assertions: etree._Element, destination: str = ACS) -> str:
    r = etree.Element(
        f"{{{NS['samlp']}}}Response",
        nsmap=NS,
        ID="_" + uuid.uuid4().hex,
        Version="2.0",
        IssueInstant=ts(datetime.now(UTC)),
        Destination=destination,
        InResponseTo=request_id,
    )
    etree.SubElement(r, f"{{{NS['saml']}}}Issuer").text = IDP
    status = etree.SubElement(r, f"{{{NS['samlp']}}}Status")
    etree.SubElement(
        status, f"{{{NS['samlp']}}}StatusCode", Value="urn:oasis:names:tc:SAML:2.0:status:Success"
    )
    for a in assertions:
        r.append(a)
    return base64.b64encode(etree.tostring(r)).decode()


def test_saml_sp_profile(rt_url: str) -> None:
    tenant, alice, bob, admin = uid(), uid(), uid(), uid()
    web = f"web-{uid()}"
    key, cert = keypair()
    rogue_key, rogue_cert = keypair()
    ring = KeyRing(LocalSigner("issuer-1"))

    async def seed() -> str:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            for p in (alice, bob, admin):
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
            for p, nid in ((alice, "alice-nid"), (bob, "bob-nid")):
                await create_external_link(
                    c,
                    tenant_id=tenant,
                    principal_id=p,
                    issuer=IDP,
                    subject=nid,
                    connection_id=None,
                    link_assurance="ENTERPRISE_PROVISIONED",
                    now=datetime.now(UTC),
                )
            await make_admin(
                c, tenant, admin, ["tenant.identity_provider.change"], ["federation_connection:*"]
            )
            sid, now = uid(), datetime.now(UTC)
            p, _ = await load_principal(c, admin)
            await create_session(
                c,
                Session(
                    sid,
                    tenant,
                    admin,
                    now - timedelta(seconds=10),
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

    admin_sid = asyncio.run(seed())
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
            session_id=admin_sid,
        )
    }

    with TestClient(app, base_url=ISS, follow_redirects=False) as http:
        r = http.post(
            "/federation/integrations",
            headers=adm,
            json={
                "protocol": "SAML",
                "display_name": "Acme ADFS",
                "issuer": IDP,
                "sso_url": "https://saml.acme.example/sso",
                "idp_certificates": [cert],
                "sp_entity_id": SP,
                "acr_levels": {PPT: 1},
                "metadata_expires_at": (datetime.now(UTC) + timedelta(days=30)).isoformat(),
            },
        )
        assert r.status_code == 201, r.text
        conn_id = r.json()["connection_id"]

        def start() -> tuple[str, str]:
            r = http.get(f"/federation/connections/{conn_id}/login", params={"client_id": web})
            assert r.status_code == 303, r.text
            q = {k: v[0] for k, v in parse_qs(urlsplit(r.headers["location"]).query).items()}
            req = etree.fromstring(zlib.decompress(base64.b64decode(q["SAMLRequest"]), -15))
            assert req.get("AssertionConsumerServiceURL") == ACS and req.get(
                "Destination"
            ).endswith("/sso")
            assert req.find("saml:Issuer", NS).text == SP
            return req.get("ID"), q["RelayState"]

        def acs(saml_response: str, relay: str | None) -> Any:
            data = {"SAMLResponse": saml_response} | ({"RelayState": relay} if relay else {})
            return http.post("/federation/saml/acs", data=data, headers=FORM)

        def code(r: Any) -> str:
            return r.json()["error"]["code"]

        # happy path: the issuer+NameID link decides the principal; context maps to AAL-Z1
        rid, relay = start()
        r = acs(response(rid, sign(assertion(rid, "alice-nid"), key, cert)), relay)
        assert r.status_code == 200, r.text
        assert r.json()["principal_id"] == alice and r.json()["aal"] == 1
        assert r.json()["authentication_context"] == f"saml:{IDP}"

        # signature wrapping: a forged unsigned assertion for bob placed first is never read
        rid, relay = start()
        forged = assertion(rid, "bob-nid")
        r = acs(response(rid, forged, sign(assertion(rid, "alice-nid"), key, cert)), relay)
        assert r.status_code == 200 and r.json()["principal_id"] == alice

        cases = [
            (lambda rid: response(rid, assertion(rid, "alice-nid")), "SAML_SIGNATURE_INVALID"),
            (
                lambda rid: response(rid, sign(assertion(rid, "alice-nid"), rogue_key, rogue_cert)),
                "SAML_SIGNATURE_INVALID",
            ),
            (
                lambda rid: response(rid, sign(assertion(rid, "alice-nid"), key, cert, alg="sha1")),
                "SAML_SIGNATURE_INVALID",
            ),
            (
                lambda rid: response(
                    rid, sign(assertion(rid, "alice-nid", audience="https://other-sp"), key, cert)
                ),
                "SAML_AUDIENCE_MISMATCH",
            ),
            (
                lambda rid: response(
                    rid, sign(assertion(rid, "alice-nid", recipient="https://evil/acs"), key, cert)
                ),
                "SAML_RECIPIENT_MISMATCH",
            ),
            (
                lambda rid: response(
                    rid, sign(assertion(rid, "alice-nid", irt="_other"), key, cert)
                ),
                "SAML_IN_RESPONSE_TO_MISMATCH",
            ),
            (
                lambda rid: response(
                    rid, sign(assertion(rid, "alice-nid", ttl=timedelta(minutes=-10)), key, cert)
                ),
                "SAML_ASSERTION_EXPIRED",
            ),
            (
                lambda rid: response(
                    rid,
                    sign(
                        assertion(
                            rid,
                            "alice-nid",
                            fmt="urn:oasis:names:tc:SAML:2.0:nameid-format:transient",
                        ),
                        key,
                        cert,
                    ),
                ),
                "SAML_NAMEID_NOT_STABLE",
            ),
            (
                lambda rid: response(
                    rid, sign(assertion(rid, "alice-nid", issuer="https://other-idp"), key, cert)
                ),
                "SAML_ISSUER_MISMATCH",
            ),
            (
                lambda rid: response(
                    rid,
                    sign(assertion(rid, "alice-nid"), key, cert),
                    destination="https://evil/acs",
                ),
                "SAML_DESTINATION_MISMATCH",
            ),
            (
                lambda rid: response(rid, sign(assertion(rid, "nobody-nid"), key, cert)),
                "FEDERATION_ACCOUNT_NOT_LINKED",
            ),
        ]
        for build, expected in cases:
            rid, relay = start()
            r = acs(build(rid), relay)
            assert r.status_code in (401, 409) and code(r) == expected, (expected, r.text)

        # XXE / DTD never reaches the signature verifier
        rid, relay = start()
        xxe = (
            b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
            b'<samlp:Response xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol">&x;</samlp:Response>'
        )
        assert code(acs(base64.b64encode(xxe).decode(), relay)) == "SAML_RESPONSE_INVALID"

        # AUTH-10 replay: a spent RelayState, and the same assertion ID in a new transaction
        rid, relay = start()
        signed = sign(assertion(rid, "alice-nid", assertion_id="_fixed-id"), key, cert)
        body = response(rid, signed)
        assert acs(body, relay).status_code == 200
        assert code(acs(body, relay)) == "FEDERATION_STATE_INVALID"
        rid2, relay2 = start()
        again = sign(assertion(rid2, "alice-nid", assertion_id="_fixed-id"), key, cert)
        assert code(acs(response(rid2, again), relay2)) == "SAML_ASSERTION_REPLAYED"

        # IdP-initiated (unsolicited) responses are refused
        assert (
            code(acs(response("_none", sign(assertion("_none", "alice-nid"), key, cert)), None))
            == "SAML_UNSOLICITED"
        )

        # AUTH-15 certificate rollover: after rotation the retired certificate verifies nothing
        new_key, new_cert = keypair()
        r = http.post(
            f"/federation/integrations/{conn_id}:rotate",
            headers=adm,
            json={
                "idp_certificates": [new_cert],
                "metadata_expires_at": (datetime.now(UTC) + timedelta(days=30)).isoformat(),
            },
        )
        assert r.status_code == 200
        rid, relay = start()
        assert (
            code(acs(response(rid, sign(assertion(rid, "alice-nid"), key, cert)), relay))
            == "SAML_SIGNATURE_INVALID"
        )
        rid, relay = start()
        assert (
            acs(
                response(rid, sign(assertion(rid, "alice-nid"), new_key, new_cert)), relay
            ).status_code
            == 200
        )
