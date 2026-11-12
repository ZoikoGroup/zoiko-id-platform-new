"""Test fixtures: real certificates, real PKCS#7 signatures embedded in real PDF byte ranges, and trusted-list XML.

Everything here is generated with `cryptography` (and `signxml` for the list signature); nothing is mocked, so the validator under test is
exercised against genuine CMS bytes and every break is made by changing exactly one property.
"""

from __future__ import annotations

import base64
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.hazmat.primitives.serialization import Encoding, pkcs7
from cryptography.x509.oid import NameOID, ObjectIdentifier
from lxml import etree
from signxml import XMLSigner, methods
from zid_platform.modules.evidence.application import cms

NOW = datetime.now(UTC).replace(microsecond=0)
OID_QC_STATEMENTS = ObjectIdentifier("1.3.6.1.5.5.7.1.3")


def der(tag: int, body: bytes) -> bytes:
    return bytes([tag]) + cms.info_len(body) + body


def _oid_der(dotted: str) -> bytes:
    parts = [int(x) for x in dotted.split(".")]
    body = bytes([parts[0] * 40 + parts[1]])
    for n in parts[2:]:
        chunk = [n & 0x7F]
        n >>= 7
        while n:
            chunk.append((n & 0x7F) | 0x80)
            n >>= 7
        body += bytes(reversed(chunk))
    return der(0x06, body)


def qc_statements(
    kind: str | None = "esign", *, compliance: bool = True
) -> x509.UnrecognizedExtension:
    """A QcStatements extension: qcCompliance and (optionally) the type, signature or seal."""
    statements = b""
    if compliance:
        statements += der(0x30, _oid_der("0.4.0.1862.1.1"))
    if kind:
        statements += der(
            0x30,
            _oid_der("0.4.0.1862.1.6")
            + der(0x30, _oid_der("0.4.0.1862.1.6.1" if kind == "esign" else "0.4.0.1862.1.6.2")),
        )
    return x509.UnrecognizedExtension(OID_QC_STATEMENTS, der(0x30, statements))


@dataclass
class Party:
    key: object
    cert: x509.Certificate

    def pem(self) -> bytes:
        return self.cert.public_bytes(Encoding.PEM)


def _name(common: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common)])


def make_cert(
    subject: str,
    key: object,
    issuer: Party | None = None,
    *,
    ca: bool = False,
    not_before: datetime = NOW - timedelta(days=60),
    not_after: datetime = NOW + timedelta(days=300),
    extensions: tuple[x509.UnrecognizedExtension, ...] = (),
    path_length: int | None = None,
) -> Party:
    issuer_name = issuer.cert.subject if issuer else _name(subject)
    signer_key = issuer.key if issuer else key
    b = (
        x509.CertificateBuilder()
        .subject_name(_name(subject))
        .issuer_name(issuer_name)
        .public_key(key.public_key())  # type: ignore[attr-defined]
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_before)
        .not_valid_after(not_after)
        .add_extension(
            x509.BasicConstraints(ca=ca, path_length=path_length if ca else None), critical=True
        )
    )
    for ext in extensions:
        b = b.add_extension(ext, critical=False)
    return Party(key, b.sign(signer_key, hashes.SHA256()))  # type: ignore[arg-type]


def ec_key() -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(ec.SECP256R1())


def rsa_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(65537, 2048)


@dataclass
class Pki:
    root: Party
    signer: Party
    intermediate: Party | None = None

    @property
    def pool(self) -> list[x509.Certificate]:
        return [self.intermediate.cert] if self.intermediate else []


def pki(
    *,
    intermediate: bool = False,
    signer_key: object | None = None,
    signer_extensions: tuple = (),
    **signer_dates: datetime,
) -> Pki:
    root = make_cert("Test Root CA", ec_key(), ca=True)
    issuer, mid = root, None
    if intermediate:
        mid = make_cert("Test Intermediate CA", ec_key(), root, ca=True)
        issuer = mid
    signer = make_cert(
        "Dana Lee (signer)",
        signer_key or ec_key(),
        issuer,
        extensions=signer_extensions,
        **signer_dates,
    )
    return Pki(root, signer, mid)


# ---------------------------------------------------------------------------------------------------------- PDF

CONTENTS_BYTES = 8192


def pdf_template() -> tuple[bytes, bytes]:
    """(head, tail) around the signature placeholder; the ByteRange numbers have fixed width so they can be filled in place."""
    head = (
        b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog /Pages 3 0 R >>\nendobj\n"
        b"2 0 obj\n<< /Type /Sig /Filter /Adobe.PPKLite /SubFilter /adbe.pkcs7.detached "
        b"/ByteRange [0000000000 0000000000 0000000000 0000000000] /Contents "
    )
    tail = b" /M (D:20260101000000Z) >>\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF\n"
    return head, tail


def sign_pdf(
    party: Pki,
    *,
    digest: hashes.HashAlgorithm | None = None,
    certs: list[x509.Certificate] | None = None,
    tamper_document: bool = False,
    token_for: Callable[[bytes], bytes] | None = None,
    key: object | None = None,
) -> bytes:
    """A PDF whose /Contents is a detached PKCS#7 signature over the two byte ranges around it."""
    head, tail = pdf_template()
    hexlen = CONTENTS_BYTES * 2 + 2
    a, b = 0, len(head)
    c = b + hexlen
    d = len(tail)
    filled = head.replace(
        b"[0000000000 0000000000 0000000000 0000000000]",
        f"[{a:010d} {b:010d} {c:010d} {d:010d}]".encode(),
    )
    placeholder = b"<" + b"0" * (hexlen - 2) + b">"
    signed = filled + tail  # the bytes the signature covers, with the placeholder removed
    builder = pkcs7.PKCS7SignatureBuilder().set_data(signed)
    builder = builder.add_signer(
        party.signer.cert, key or party.signer.key, digest or hashes.SHA256()
    )  # type: ignore[arg-type]
    for extra in certs if certs is not None else party.pool:
        builder = builder.add_certificate(extra)
    cms_der = builder.sign(
        Encoding.DER, [pkcs7.PKCS7Options.DetachedSignature, pkcs7.PKCS7Options.Binary]
    )
    if (
        token_for is not None
    ):  # ECDSA is not deterministic: stamp THIS signature value, never a second signing of the same bytes
        _, signer = cms._parse_cms(cms_der)
        cms_der = add_timestamp(cms_der, token_for(signer.signature))
    assert len(cms_der) <= CONTENTS_BYTES, "the fixture placeholder is too small"
    hexed = b"<" + cms_der.hex().encode().ljust(hexlen - 2, b"0") + b">"
    out = filled + hexed + tail
    assert len(out) == len(filled) + len(placeholder) + len(tail)
    if tamper_document:
        i = out.index(b"/Type /Catalog") + 3
        out = out[:i] + bytes([out[i] ^ 0x01]) + out[i + 1 :]
    return out


def signature_hex_span(pdf: bytes) -> tuple[int, int]:
    start = pdf.index(b"/Contents <") + len(b"/Contents <")
    return start, pdf.index(b">", start)


def cms_of(pdf: bytes) -> bytes:
    (_, cms_bytes), *_ = cms.signature_slots(pdf)
    return cms_bytes


def flip_signature_value(pdf: bytes) -> bytes:
    """Change one byte of the signature VALUE inside the CMS, leaving every length and the signed content untouched."""
    start, _ = signature_hex_span(pdf)
    der_bytes = cms_of(pdf)
    _certs, signer = cms._parse_cms(der_bytes)
    at = der_bytes.rindex(signer.signature)
    mutated = bytearray(der_bytes)
    mutated[at + 5] ^= 0xFF
    new_hex = bytes(mutated).hex().encode()
    return pdf[:start] + new_hex + pdf[start + len(new_hex) :]


def append_after_signing(pdf: bytes) -> bytes:
    return pdf + b"3 0 obj\n<< /Type /Pages /Count 0 >>\nendobj\n%%EOF\n"


# ---------------------------------------------------------------------------------------------------------- timestamp


def add_timestamp(cms_der: bytes, token_content_info: bytes) -> bytes:
    """Add an RFC 3161 token as an unsigned attribute of the signer, re-encoding every enclosing length."""
    _, outer, _, _ = cms._tlv(cms_der, 0)
    oid_el, wrapper = cms._children(outer)
    signed_data = cms._children(cms._children(wrapper[1])[0][1])
    fixed, signer_set = signed_data[:-1], signed_data[-1]
    (signer_info,) = cms._children(signer_set[1])
    _, info_body, _, _ = cms._tlv(signer_info[2], 0)
    attr = der(0x30, _oid_der(cms.OID_TIMESTAMP_TOKEN) + der(0x31, token_content_info))
    new_info = der(0x30, info_body + der(0xA1, attr))
    new_signed = der(0x30, b"".join(w for _, _, w in fixed) + der(0x31, new_info))
    return der(0x30, oid_el[2] + der(0xA0, new_signed))


def token_from_response(resp: bytes) -> bytes:
    """The TimeStampToken (a ContentInfo) inside a granted TimeStampResp."""
    _, body, _, _ = cms._tlv(resp, 0)
    return cms._children(body)[1][2]


# ---------------------------------------------------------------------------------------------------------- CRL


def make_crl(
    issuer: Party,
    revoked: list[tuple[x509.Certificate, datetime]],
    *,
    last_update: datetime | None = None,
) -> x509.CertificateRevocationList:
    b = (
        x509.CertificateRevocationListBuilder()
        .issuer_name(issuer.cert.subject)
        .last_update(last_update or NOW - timedelta(days=1))
        .next_update(NOW + timedelta(days=30))
    )
    for cert, when in revoked:
        b = b.add_revoked_certificate(
            x509.RevokedCertificateBuilder()
            .serial_number(cert.serial_number)
            .revocation_date(when)
            .build()
        )
    return b.sign(issuer.key, hashes.SHA256())  # type: ignore[arg-type]


# ---------------------------------------------------------------------------------------------------------- trusted list

TL_NS = "http://uri.etsi.org/02231/v2#"
SVC_QC = "http://uri.etsi.org/TrstSvc/Svctype/CA/QC"
GRANTED = "http://uri.etsi.org/TrstSvc/TrustedList/Svcstatus/granted"
WITHDRAWN = "http://uri.etsi.org/TrstSvc/TrustedList/Svcstatus/withdrawn"
FOR_ESIG = "http://uri.etsi.org/TrstSvc/TrustedList/SvcInfoExt/ForeSignatures"


def trusted_list_xml(
    ca: Party,
    *,
    status: str = GRANTED,
    since: datetime = datetime(2020, 1, 1, tzinfo=UTC),
    history: list[tuple[str, datetime]] | None = None,
    service_type: str = SVC_QC,
    sign_with: Party | None = None,
) -> bytes:
    b64 = base64.b64encode(ca.cert.public_bytes(Encoding.DER)).decode()
    hist = "".join(
        f"<ServiceHistoryInstance><ServiceStatus>{s}</ServiceStatus><StatusStartingTime>{t:%Y-%m-%dT%H:%M:%SZ}</StatusStartingTime></ServiceHistoryInstance>"
        for s, t in (history or [])
    )
    xml = (
        f'<TrustServiceStatusList xmlns="{TL_NS}"><TrustServiceProviderList><TrustServiceProvider><TSPServices><TSPService>'
        f"<ServiceInformation><ServiceTypeIdentifier>{service_type}</ServiceTypeIdentifier><ServiceStatus>{status}</ServiceStatus>"
        f"<StatusStartingTime>{since:%Y-%m-%dT%H:%M:%SZ}</StatusStartingTime>"
        f"<ServiceDigitalIdentity><DigitalId><X509Certificate>{b64}</X509Certificate></DigitalId></ServiceDigitalIdentity>"
        f"<ServiceInformationExtensions><Extension><AdditionalServiceInformation><URI>{FOR_ESIG}</URI></AdditionalServiceInformation></Extension></ServiceInformationExtensions>"
        f"</ServiceInformation><ServiceHistory>{hist}</ServiceHistory></TSPService></TSPServices></TrustServiceProvider></TrustServiceProviderList></TrustServiceStatusList>"
    )
    if sign_with is None:
        return xml.encode()
    root = etree.fromstring(xml.encode())
    key_pem = sign_with.key.private_bytes(
        Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )  # type: ignore[attr-defined]
    signed = XMLSigner(
        method=methods.enveloped, signature_algorithm="rsa-sha256", digest_algorithm="sha256"
    ).sign(root, key=key_pem, cert=sign_with.pem().decode())
    return etree.tostring(signed)


def list_of_lists_xml(
    pointers: list[tuple[str, str, Party]], *, sign_with: Party | None = None
) -> bytes:
    """An EU-style list of lists: one OtherTSLPointer per (territory, https location, the national list's signer)."""
    items = "".join(
        f"<OtherTSLPointer><ServiceDigitalIdentities><ServiceDigitalIdentity><DigitalId><X509Certificate>"
        f"{base64.b64encode(signer.cert.public_bytes(Encoding.DER)).decode()}</X509Certificate></DigitalId></ServiceDigitalIdentity>"
        f"</ServiceDigitalIdentities><TSLLocation>{location}</TSLLocation><AdditionalInformation>"
        f"<OtherInformation><TSLType>http://uri.etsi.org/TrstSvc/TrustedList/TSLType/EUgeneric</TSLType></OtherInformation>"
        f"<OtherInformation><SchemeTerritory>{territory}</SchemeTerritory></OtherInformation>"
        f"<OtherInformation><MimeType>application/vnd.etsi.tsl+xml</MimeType></OtherInformation>"
        f"</AdditionalInformation></OtherTSLPointer>"
        for territory, location, signer in pointers
    )
    xml = f'<TrustServiceStatusList xmlns="{TL_NS}"><SchemeInformation><PointersToOtherTSL>{items}</PointersToOtherTSL></SchemeInformation></TrustServiceStatusList>'
    if sign_with is None:
        return xml.encode()
    root = etree.fromstring(xml.encode())
    key_pem = sign_with.key.private_bytes(
        Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )  # type: ignore[attr-defined]
    signed = XMLSigner(
        method=methods.enveloped, signature_algorithm="rsa-sha256", digest_algorithm="sha256"
    ).sign(root, key=key_pem, cert=sign_with.pem().decode())
    return etree.tostring(signed)


# ---------------------------------------------------------------------------------------------------------- OCSP


def make_ocsp(
    cert: x509.Certificate,
    issuer: Party,
    *,
    status: str = "good",
    revoked_at: datetime | None = None,
    signer: Party | None = None,
    include_signer_cert: bool = False,
    this_update: datetime | None = None,
    next_update: datetime | None = None,
    sign_hash: hashes.HashAlgorithm | None = None,
) -> bytes:
    """A real DER OCSP response for `cert`, signed by `signer` (default: the issuer itself)."""
    from cryptography.x509 import ocsp

    b = ocsp.OCSPResponseBuilder().add_response(
        cert=cert,
        issuer=issuer.cert,
        algorithm=hashes.SHA1(),
        cert_status={
            "good": ocsp.OCSPCertStatus.GOOD,
            "revoked": ocsp.OCSPCertStatus.REVOKED,
            "unknown": ocsp.OCSPCertStatus.UNKNOWN,
        }[status],
        this_update=this_update or NOW - timedelta(hours=1),
        next_update=next_update or NOW + timedelta(days=2),
        revocation_time=revoked_at if status == "revoked" else None,
        revocation_reason=None,
    )
    who = signer or issuer
    b = b.responder_id(ocsp.OCSPResponderEncoding.HASH, who.cert)
    if include_signer_cert and signer is not None:
        b = b.certificates([signer.cert])
    return b.sign(who.key, sign_hash or hashes.SHA256()).public_bytes(Encoding.DER)  # type: ignore[arg-type]


def ocsp_signing_cert(issuer: Party, subject: str = "OCSP Responder") -> Party:
    key = ec_key()
    b = (
        x509.CertificateBuilder()
        .subject_name(_name(subject))
        .issuer_name(issuer.cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(NOW - timedelta(days=30))
        .not_valid_after(NOW + timedelta(days=60))
        .add_extension(
            x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.OCSP_SIGNING]), critical=False
        )
    )
    return Party(key, b.sign(issuer.key, hashes.SHA256()))  # type: ignore[arg-type]
