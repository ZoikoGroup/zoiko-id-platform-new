"""SAML 2.0 SP adapter (ZID-AUTH-001 §10; AUTH-W5; AUTH-03, AUTH-10).

    begin_saml()     AuthnRequest over HTTP-Redirect; RelayState = single-use login transaction
    complete_saml()  ACS (HTTP-POST): hardened parse -> [EncryptedAssertion: AES-GCM/RSA-OAEP
                     decrypt with the brokered SP key] -> signature with a pinned IdP certificate
                     (SHA-256+ only) -> facts read ONLY from the signed element -> exact
                     Destination/Recipient/Audience, InResponseTo, time window, stable NameID ->
                     assertion-ID replay cache -> account link -> identity session

Encrypted assertions: either the decrypted assertion is itself signed, or the signed Response
carries the EncryptedAssertion (the ciphertext is then covered by the IdP signature and GCM).

IdP-initiated SSO is off unless an approved R3 change enabled it for one connection. Unsolicited
responses then go to that connection's own ACS (/federation/saml/acs/{tenant}.{connection}), so
Destination/Recipient name it exactly; they must be fresh and carry no InResponseTo; the
assertion-ID replay cache applies; and RelayState may only name one of the approved landing URLs
- never an arbitrary redirect. Library choice:
signxml (pure Python, returns only what was signed) instead of pysaml2, whose verification needs
the external xmlsec1 binary; recorded as an open DEC-E1 point.
"""

from __future__ import annotations

import base64
import secrets
import uuid
import zlib
from dataclasses import replace
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
from zid_platform.modules.federation.application.saml_decrypt import decrypt_assertion, load_key
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


def connection_acs_url(settings: FederationSettings, c: Connection) -> str:
    """Per-connection ACS for governed IdP-initiated SSO (no RelayState to route by)."""
    return f"{acs_url(settings)}/{c.tenant_id}.{c.connection_id}"


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


def _parse(xml: bytes) -> etree._Element:
    """Only ever called on bytes that already passed the defusedxml gate."""
    return etree.fromstring(xml, parser=etree.XMLParser(resolve_entities=False, no_network=True))


def _safe(xml: bytes, error: str) -> None:
    try:  # no DTDs, no entities, no external references - before any other parser sees it
        SafeET.fromstring(xml, forbid_dtd=True, forbid_entities=True, forbid_external=True)
    except (DefusedXmlException, ParseError, ValueError) as e:
        raise FederationError(error, type(e).__name__) from e


def _authenticated(
    xml: bytes, c: Connection, settings: FederationSettings
) -> tuple[etree._Element, etree._Element]:
    """(envelope, assertion): the assertion is always one the IdP signature covers."""
    root = _parse(xml)
    plain = root.findall("saml:Assertion", NS)
    encrypted = root.findall("saml:EncryptedAssertion", NS)
    if c.require_encrypted_assertions and (plain or not encrypted):
        raise FederationError("SAML_ASSERTION_NOT_ENCRYPTED")
    if not encrypted:
        signed = _verified(xml, c)
        tag = etree.QName(signed).localname
        if tag == "Assertion":
            return root, signed
        if tag != "Response":
            raise FederationError("SAML_SIGNATURE_INVALID", "unexpected signed element")
        found = signed.findall("saml:Assertion", NS)
        if len(found) != 1:
            raise FederationError("SAML_ASSERTION_INVALID", "exactly one assertion")
        return signed, found[0]
    if plain or len(encrypted) != 1:
        raise FederationError("SAML_ASSERTION_INVALID", "exactly one assertion")
    if not c.sp_decryption_key_ref:
        raise FederationError("SAML_DECRYPTION_FAILED", "no SP decryption key")
    key = load_key(settings.secrets(c.sp_decryption_key_ref))
    try:  # a signed Response covering the ciphertext
        outer: etree._Element | None = _verified(xml, c)
    except FederationError:
        outer = None
    if outer is not None and etree.QName(outer).localname == "Response":
        found = outer.findall("saml:EncryptedAssertion", NS)
        if len(found) != 1:
            raise FederationError("SAML_ASSERTION_INVALID", "exactly one assertion")
        return outer, _decrypted(decrypt_assertion(found[0], key))
    plaintext = decrypt_assertion(encrypted[0], key)
    _decrypted(plaintext)
    signed = _verified(plaintext, c)  # otherwise the assertion itself must be signed
    if etree.QName(signed).localname != "Assertion":
        raise FederationError("SAML_SIGNATURE_INVALID", "unexpected signed element")
    return root, signed


def _decrypted(plaintext: bytes) -> etree._Element:
    _safe(plaintext, "SAML_DECRYPTION_FAILED")
    assertion = _parse(plaintext)
    if etree.QName(assertion).text != f"{{{NS['saml']}}}Assertion":
        raise FederationError("SAML_DECRYPTION_FAILED", "not an assertion")
    return assertion


def _facts(envelope: etree._Element, assertion: etree._Element) -> AssertionFacts:
    if etree.QName(envelope).text != f"{{{NS['samlp']}}}Response":
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
        # unsolicited at the shared ACS: never accepted, even where IdP-initiated SSO is enabled
        raise FederationError("SAML_UNSOLICITED")
    ctx = base_ctx.with_identity(tenant, None)
    async with tenant_transaction(engine, ctx) as conn:
        login = await store.finish_login(conn, relay_state or "", now)
        c = await store.load(conn, login.connection_id) if login else None
    if login is None or c is None:
        raise FederationError("FEDERATION_STATE_INVALID")
    usable(c, now)
    if c.protocol != "SAML" or c.config_revision != login.config_revision:
        raise FederationError("FEDERATION_CONFIG_CHANGED")
    facts = _facts(*_authenticated(_decode(saml_response), c, settings))
    valid_until = check_assertion(
        facts,
        issuer=c.issuer,
        acs_url=acs_url(settings),
        sp_entity_id=c.sp_entity_id or "",
        request_id=login.request_id,
        now=now,
    )
    return await _finish(engine, settings, c, facts, valid_until, login, ctx, now)


async def complete_unsolicited_saml(
    engine: AsyncEngine,
    settings: FederationSettings,
    *,
    connection_ref: str,
    saml_response: str | None,
    relay_state: str | None,
    base_ctx: RequestContext,
    now: datetime,
) -> LoggedIn:
    """Governed IdP-initiated SSO at the connection's own ACS."""
    tenant, _, connection_id = connection_ref.partition(".")
    try:
        tenant = str(uuid.UUID(tenant))
    except ValueError as e:
        raise FederationError("FEDERATION_CONNECTION_NOT_FOUND") from e
    ctx = base_ctx.with_identity(tenant, None)
    async with tenant_transaction(engine, ctx) as conn:
        c = usable(await store.load(conn, connection_id), now)
    if c.protocol != "SAML" or not c.allow_idp_initiated:
        raise FederationError("SAML_UNSOLICITED")
    return_to = None
    if relay_state:  # a landing target, never an open redirect
        if relay_state not in c.idp_initiated_targets:
            raise FederationError("SAML_RELAY_STATE_REJECTED")
        return_to = relay_state
    facts = _facts(*_authenticated(_decode(saml_response), c, settings))
    valid_until = check_assertion(
        facts,
        issuer=c.issuer,
        acs_url=connection_acs_url(settings, c),
        sp_entity_id=c.sp_entity_id or "",
        request_id=None,
        now=now,
        unsolicited=True,
    )
    done = await _finish(engine, settings, c, facts, valid_until, None, ctx, now)
    return replace(done, return_to=return_to)


def _decode(saml_response: str | None) -> bytes:
    try:
        xml = base64.b64decode(saml_response or "", validate=True)
    except ValueError as e:
        raise FederationError("SAML_RESPONSE_INVALID") from e
    if not xml or len(xml) > MAX_RESPONSE_BYTES:
        raise FederationError("SAML_RESPONSE_INVALID")
    _safe(xml, "SAML_RESPONSE_INVALID")
    return xml


async def _finish(
    engine: AsyncEngine,
    settings: FederationSettings,
    c: Connection,
    facts: AssertionFacts,
    valid_until: datetime,
    login: store.Login | None,
    ctx: RequestContext,
    now: datetime,
) -> LoggedIn:
    tenant = c.tenant_id
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
