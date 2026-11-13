"""Test fixtures: real XAdES-BES signatures built by hand with lxml and `cryptography` (no signing library), so the verifier under test
(which uses signxml) is checked against bytes it did not produce.

Every break is made by changing exactly one property of an otherwise valid signed document.
"""

from __future__ import annotations

import base64
import hashlib
from collections.abc import Callable
from datetime import timedelta

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.serialization import Encoding
from lxml import etree
from pdf_signing import NOW, Party

DS_NS = "http://www.w3.org/2000/09/xmldsig#"
XA_NS = "http://uri.etsi.org/01903/v1.3.2#"
EXC = "http://www.w3.org/2001/10/xml-exc-c14n#"
SHA256 = "http://www.w3.org/2001/04/xmlenc#sha256"
ECDSA_SHA256 = "http://www.w3.org/2001/04/xmldsig-more#ecdsa-sha256"
ENVELOPED = "http://www.w3.org/2000/09/xmldsig#enveloped-signature"
SP_TYPE = "http://uri.etsi.org/01903#SignedProperties"


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


def _c14n(el: etree._Element) -> bytes:
    return etree.tostring(el, method="c14n", exclusive=True, with_comments=False)


def _el(parent: etree._Element | None, ns: str, name: str, text: str | None = None, **attrs: str):
    el = (
        etree.Element(f"{{{ns}}}{name}", nsmap=None)
        if parent is None
        else etree.SubElement(parent, f"{{{ns}}}{name}")
    )
    for k, v in attrs.items():
        el.set(k, v)
    if text is not None:
        el.text = text
    return el


def sign_xml(
    party: Party,
    *,
    pool: list | None = None,
    body: str = "<data>hello</data>",
    sign_properties: bool = True,
    cert_digest_of=None,
    signing_time: str | None = None,
    second_signature: bool = False,
    base: bytes | None = None,
    n: int = 1,
    over_id: str | None = None,
    tamper_value: bool = False,
) -> bytes:
    """An enveloped XAdES-BES signature over the whole document and (when `sign_properties`) over its SignedProperties."""
    root = (
        etree.fromstring(base)
        if base is not None
        else etree.fromstring(f'<doc xmlns="urn:example" Id="doc">{body}</doc>')
    )
    sig = etree.SubElement(root, f"{{{DS_NS}}}Signature", nsmap={"ds": DS_NS, "xades": XA_NS})
    sig.set("Id", f"sig-{n}")
    info = _el(sig, DS_NS, "SignedInfo")
    _el(info, DS_NS, "CanonicalizationMethod", Algorithm=EXC)
    _el(info, DS_NS, "SignatureMethod", Algorithm=ECDSA_SHA256)

    def reference(uri: str, transforms: list[str], type_: str | None = None):
        ref = _el(info, DS_NS, "Reference", URI=uri)
        if type_:
            ref.set("Type", type_)
        tr = _el(ref, DS_NS, "Transforms")
        for t in transforms:
            _el(tr, DS_NS, "Transform", Algorithm=t)
        _el(ref, DS_NS, "DigestMethod", Algorithm=SHA256)
        return _el(ref, DS_NS, "DigestValue", "")

    doc_digest = (
        reference("", [ENVELOPED, EXC]) if over_id is None else reference(f"#{over_id}", [EXC])
    )
    sp_digest = reference(f"#sp-{n}", [EXC], SP_TYPE) if sign_properties else None
    value = _el(sig, DS_NS, "SignatureValue", "", Id=f"sigvalue-{n}")
    key_info = _el(sig, DS_NS, "KeyInfo")
    x509 = _el(key_info, DS_NS, "X509Data")
    _el(x509, DS_NS, "X509Certificate", _b64(party.cert.public_bytes(Encoding.DER)))
    for c in pool or []:
        _el(x509, DS_NS, "X509Certificate", _b64(c.public_bytes(Encoding.DER)))
    obj = _el(sig, DS_NS, "Object")
    qp = _el(obj, XA_NS, "QualifyingProperties", Target=f"#sig-{n}")
    sp = _el(qp, XA_NS, "SignedProperties", Id=f"sp-{n}")
    ssp = _el(sp, XA_NS, "SignedSignatureProperties")
    _el(
        ssp,
        XA_NS,
        "SigningTime",
        signing_time or (NOW - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    cert_for_digest = cert_digest_of or party.cert
    sc = _el(ssp, XA_NS, "SigningCertificateV2")
    cert = _el(sc, XA_NS, "Cert")
    cd = _el(cert, XA_NS, "CertDigest")
    _el(cd, DS_NS, "DigestMethod", Algorithm=SHA256)
    _el(
        cd,
        DS_NS,
        "DigestValue",
        _b64(hashlib.sha256(cert_for_digest.public_bytes(Encoding.DER)).digest()),
    )

    # digests: the document without its signature, then the signed properties, then SignedInfo is signed
    without = etree.fromstring(etree.tostring(root))
    for s in list(without.iter(f"{{{DS_NS}}}Signature")):
        if (
            s.get("Id") == f"sig-{n}"
        ):  # the enveloped transform removes only the signature being made
            s.getparent().remove(s)
    if over_id is None:
        doc_digest.text = _b64(hashlib.sha256(_c14n(without)).digest())
    else:
        target = next(
            e for e in without.iter() if isinstance(e.tag, str) and e.get("Id") == over_id
        )
        doc_digest.text = _b64(hashlib.sha256(_c14n(target)).digest())
    if sp_digest is not None:
        sp_digest.text = _b64(hashlib.sha256(_c14n(sp)).digest())
    r, s_ = decode_dss_signature(party.key.sign(_c14n(info), ec.ECDSA(hashes.SHA256())))  # type: ignore[attr-defined]
    raw = r.to_bytes(32, "big") + s_.to_bytes(32, "big")
    if tamper_value:
        raw = raw[:-1] + bytes([raw[-1] ^ 0x01])
    value.text = _b64(raw)
    out = etree.tostring(root)
    if second_signature:
        out = out.replace(b"</doc>", b'<ds:Signature xmlns:ds="' + DS_NS.encode() + b'"/></doc>')
    return out


def add_timestamp(xml: bytes, token_for: Callable[[bytes], bytes]) -> bytes:
    """Add an XAdES SignatureTimeStamp (unsigned property): an RFC 3161 token over the canonical SignatureValue."""
    root = etree.fromstring(xml)
    value = next(root.iter(f"{{{DS_NS}}}SignatureValue"))
    qp = next(root.iter(f"{{{XA_NS}}}QualifyingProperties"))
    unsigned = _el(qp, XA_NS, "UnsignedProperties")
    usp = _el(unsigned, XA_NS, "UnsignedSignatureProperties")
    stamp = _el(usp, XA_NS, "SignatureTimeStamp")
    _el(
        stamp,
        DS_NS,
        "CanonicalizationMethod",
        Algorithm="http://www.w3.org/TR/2001/REC-xml-c14n-20010315",
    )
    token = token_for(etree.tostring(value, method="c14n", exclusive=False, with_comments=False))
    _el(stamp, XA_NS, "EncapsulatedTimeStamp", _b64(token))
    return etree.tostring(root)


def add_ltv(xml: bytes, *, certs=(), crls=(), ocsps=()) -> bytes:
    """Embed CertificateValues and RevocationValues (unsigned properties), as a long-term XAdES signature carries them."""
    root = etree.fromstring(xml)
    qp = next(root.iter(f"{{{XA_NS}}}QualifyingProperties"))
    unsigned = qp.find(f"{{{XA_NS}}}UnsignedProperties")
    if unsigned is None:
        unsigned = _el(qp, XA_NS, "UnsignedProperties")
    usp = unsigned.find(f"{{{XA_NS}}}UnsignedSignatureProperties")
    if usp is None:
        usp = _el(unsigned, XA_NS, "UnsignedSignatureProperties")
    if certs:
        values = _el(usp, XA_NS, "CertificateValues")
        for c in certs:
            _el(values, XA_NS, "EncapsulatedX509Certificate", _b64(c.public_bytes(Encoding.DER)))
    if crls or ocsps:
        rv = _el(usp, XA_NS, "RevocationValues")
        if crls:
            holder = _el(rv, XA_NS, "CRLValues")
            for crl in crls:
                _el(holder, XA_NS, "EncapsulatedCRLValue", _b64(crl.public_bytes(Encoding.DER)))
        if ocsps:
            holder = _el(rv, XA_NS, "OCSPValues")
            for o in ocsps:
                _el(holder, XA_NS, "EncapsulatedOCSPValue", _b64(o))
    return etree.tostring(root)
