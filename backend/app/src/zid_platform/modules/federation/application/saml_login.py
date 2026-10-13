"""SAML 2.0 SP adapter (ZID-AUTH-001 §10; AUTH-W5; AUTH-03, AUTH-10).

    begin_saml()     AuthnRequest over HTTP-Redirect; RelayState = single-use login transaction
    complete_saml()  ACS (HTTP-POST): hardened parse -> signature with a pinned IdP certificate
                     (SHA-256+ only) -> facts read ONLY from the signed element -> exact
                     Destination/Recipient/Audience, InResponseTo, time window, stable NameID ->
                     assertion-ID replay cache -> account link -> identity session

IdP-initiated SSO is disabled (no RelayState, no login transaction, no session). Library choice:
signxml (pure Python, returns only what was signed) instead of pysaml2, whose verification needs
the external xmlsec1 binary; recorded as an open DEC-E1 point.
"""

from __future__ import annotations

import base64
import secrets
import zlib
from datetime import UTC, datetime
from urllib.parse import urlencode
from xml.etree.ElementTree import ParseError

from defusedxml import DefusedXmlException
from defusedxml import ElementTree as SafeET
from lxml import etree
from signxml.algorithms import DigestAlgorithm, SignatureMethod
from signxml.exceptions import InvalidInput, InvalidSignature
from signxml.verifier import SignatureConfiguration, XMLVerifier
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine
from zid_context import RequestContext
from zid_db import tenant_transaction

from zid_platform.modules.federation.application.oidc_login import (
    FederationSettings,
    LoggedIn,
    establish,
)
from zid_platform.modules.federation.domain.connection import (
    LOGIN_TTL,
    Connection,
    FederationError,
    UpstreamIdentity,
    usable,
)
from zid_platform.modules.federation.domain.saml import AssertionFacts, check_assertion
from zid_platform.modules.federation.infrastructure import connection_store as store

NS = {
    "samlp": "urn:oasis:names:tc:SAML:2.0:protocol",
    "saml": "urn:oasis:names:tc:SAML:2.0:assertion",
}
MAX_RESPONSE_BYTES = 256 * 1024
SIGNATURE_POLICY = SignatureConfiguration(
    require_x509=True,
    expect_references=1,
    signature_methods=frozenset(
        {
            SignatureMethod.RSA_SHA256,
            SignatureMethod.RSA_SHA384,
            SignatureMethod.RSA_SHA512,
            SignatureMethod.ECDSA_SHA256,
            SignatureMethod.ECDSA_SHA384,
            SignatureMethod.ECDSA_SHA512,
        }
    ),
    digest_algorithms=frozenset(
        {DigestAlgorithm.SHA256, DigestAlgorithm.SHA384, DigestAlgorithm.SHA512}
    ),
)


def acs_url(settings: FederationSettings) -> str:
    return settings.issuer.rstrip("/") + "/federation/saml/acs"


def _instant(now: datetime) -> str:
    return now.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


async def begin_saml(
    conn: AsyncConnection,
    settings: FederationSettings,
    c: Connection,
    *,
    client_id: str | None,
    return_to: str | None,
    link_principal_id: str | None,
    now: datetime,
) -> str:
    usable(c, now)
    if c.protocol != "SAML":
        raise FederationError("FEDERATION_PROTOCOL_MISMATCH")
    request_id = "_" + secrets.token_hex(20)
    state = await store.start_login(
        conn,
        tenant_id=c.tenant_id,
        c=c,
        nonce=None,
        code_verifier=None,
        request_id=request_id,
        client_id=client_id,
        return_to=return_to,
        link_principal_id=link_principal_id,
        expires_at=now + LOGIN_TTL,
    )
    root = etree.Element(
        f"{{{NS['samlp']}}}AuthnRequest",
        nsmap=NS,
        ID=request_id,
        Version="2.0",
        IssueInstant=_instant(now),
        Destination=c.sso_url or "",
        AssertionConsumerServiceURL=acs_url(settings),
        ProtocolBinding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST",
    )
    issuer = etree.SubElement(root, f"{{{NS['saml']}}}Issuer")
    issuer.text = c.sp_entity_id
    etree.SubElement(
        root,
        f"{{{NS['samlp']}}}NameIDPolicy",
        Format="urn:oasis:names:tc:SAML:2.0:nameid-format:persistent",
        AllowCreate="true",
    )
    deflater = zlib.compressobj(9, zlib.DEFLATED, -15)
    raw = deflater.compress(etree.tostring(root)) + deflater.flush()
    query = urlencode({"SAMLRequest": base64.b64encode(raw).decode(), "RelayState": state})
    sso = c.sso_url or ""
    return sso + ("&" if "?" in sso else "?") + query


def _time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).astimezone(UTC)
    except ValueError as e:
        raise FederationError("SAML_TIME_INVALID") from e


def _verified(xml: bytes, c: Connection) -> etree._Element:
    """The element the IdP actually signed, checked against each pinned certificate in turn."""
    for pem in c.idp_certificates:
        try:
            result = XMLVerifier().verify(xml, x509_cert=pem, expect_config=SIGNATURE_POLICY)
        except (InvalidSignature, InvalidInput):
            continue
        if isinstance(result, list):
            raise FederationError("SAML_SIGNATURE_INVALID", "exactly one signature expected")
        if result.signed_xml is None:
            raise FederationError("SAML_SIGNATURE_INVALID", "nothing was signed")
        return result.signed_xml
    raise FederationError("SAML_SIGNATURE_INVALID")


def _facts(xml: bytes, signed: etree._Element) -> AssertionFacts:
    tag = etree.QName(signed).localname
    if tag == "Assertion":
        assertion = signed
        envelope = etree.fromstring(
            xml, parser=etree.XMLParser(resolve_entities=False, no_network=True)
        )
    elif tag == "Response":
        found = signed.findall("saml:Assertion", NS)
        if len(found) != 1:
            raise FederationError("SAML_ASSERTION_INVALID", "exactly one assertion")
        assertion, envelope = found[0], signed
    else:
        raise FederationError("SAML_SIGNATURE_INVALID", "unexpected signed element")
    if etree.QName(envelope).localname != "Response":
        raise FederationError("SAML_RESPONSE_INVALID")

    def text(node: etree._Element | None, path: str) -> str | None:
        found = node.find(path, NS) if node is not None else None
        return found.text.strip() if found is not None and found.text else None

    status = envelope.find("samlp:Status/samlp:StatusCode", NS)
    name_id = assertion.find("saml:Subject/saml:NameID", NS)
    confirmation = assertion.find("saml:Subject/saml:SubjectConfirmation", NS)
    data = (
        confirmation.find("saml:SubjectConfirmationData", NS) if confirmation is not None else None
    )
    conditions = assertion.find("saml:Conditions", NS)
    authn = assertion.find("saml:AuthnStatement", NS)
    return AssertionFacts(
        response_destination=envelope.get("Destination"),
        response_in_response_to=envelope.get("InResponseTo"),
        status=status.get("Value", "") if status is not None else "",
        issuer=text(assertion, "saml:Issuer") or "",
        assertion_id=assertion.get("ID", ""),
        name_id=(name_id.text or "").strip() if name_id is not None else "",
        name_id_format=name_id.get("Format") if name_id is not None else None,
        confirmation_method=confirmation.get("Method", "") if confirmation is not None else "",
        recipient=data.get("Recipient") if data is not None else None,
        confirmation_in_response_to=data.get("InResponseTo") if data is not None else None,
        confirmation_not_on_or_after=_time(data.get("NotOnOrAfter") if data is not None else None),
        not_before=_time(conditions.get("NotBefore") if conditions is not None else None),
        not_on_or_after=_time(conditions.get("NotOnOrAfter") if conditions is not None else None),
        audiences=tuple(
            (a.text or "").strip()
            for a in (
                conditions.findall("saml:AudienceRestriction/saml:Audience", NS)
                if conditions is not None
                else []
            )
        ),
        authn_instant=_time(authn.get("AuthnInstant") if authn is not None else None),
        authn_context=text(authn, "saml:AuthnContext/saml:AuthnContextClassRef"),
    )


async def complete_saml(
    engine: AsyncEngine,
    settings: FederationSettings,
    *,
    saml_response: str | None,
    relay_state: str | None,
    base_ctx: RequestContext,
    now: datetime,
) -> LoggedIn:
    tenant = store.state_tenant(relay_state or "")
    if tenant is None:
        raise FederationError("SAML_UNSOLICITED")  # IdP-initiated: disabled by profile
    ctx = base_ctx.with_identity(tenant, None)
    async with tenant_transaction(engine, ctx) as conn:
        login = await store.finish_login(conn, relay_state or "", now)
        c = await store.load(conn, login.connection_id) if login else None
    if login is None or c is None:
        raise FederationError("FEDERATION_STATE_INVALID")
    usable(c, now)
    if c.protocol != "SAML" or c.config_revision != login.config_revision:
        raise FederationError("FEDERATION_CONFIG_CHANGED")
    try:
        xml = base64.b64decode(saml_response or "", validate=True)
    except ValueError as e:
        raise FederationError("SAML_RESPONSE_INVALID") from e
    if not xml or len(xml) > MAX_RESPONSE_BYTES:
        raise FederationError("SAML_RESPONSE_INVALID")
    try:  # no DTDs, no entities, no external references - before any other parser sees it
        SafeET.fromstring(xml, forbid_dtd=True, forbid_entities=True, forbid_external=True)
    except (DefusedXmlException, ParseError, ValueError) as e:
        raise FederationError("SAML_RESPONSE_INVALID", type(e).__name__) from e
    facts = _facts(xml, _verified(xml, c))
    valid_until = check_assertion(
        facts,
        issuer=c.issuer,
        acs_url=acs_url(settings),
        sp_entity_id=c.sp_entity_id or "",
        request_id=login.request_id,
        now=now,
    )
    async with tenant_transaction(engine, ctx) as conn:
        if not await store.record_assertion(
            conn,
            tenant_id=tenant,
            issuer=c.issuer,
            assertion_id=facts.assertion_id,
            expires_at=valid_until,
        ):
            raise FederationError("SAML_ASSERTION_REPLAYED")
    ident = UpstreamIdentity(
        issuer=c.issuer,
        subject=facts.name_id,
        acr=facts.authn_context,
        amr=(),
        auth_time=facts.authn_instant,
    )
    return await establish(engine, settings, c, ident, login, ctx=ctx, now=now)
