"""XAdES / XML-DSig signature verification, on `signxml` and `cryptography` only (ZID-INT-002 section 09; DTV-02; GID-051 e-seals).

What this verifies for the ONE signature an XML document carries:
- the XML is safe to read (no DTD, entity or external reference: refused before parsing, as for trusted lists);
- the XML-DSig signature value verifies against the signer certificate carried in `ds:KeyInfo`, and every `ds:Reference` digest matches
  (signxml refuses SHA-1 and unknown algorithms, so those are reported unsupported, never accepted);
- XAdES: the `SignedProperties` element is itself signed (otherwise the claimed signing time and signing certificate could be swapped
  without breaking the signature), and its `SigningCertificate(V2)` digest names the certificate that signed;
- the certificate chain builds to a trust anchor the operator configured, judged at the TRUSTED signing time when a `SignatureTimeStamp`
  (RFC 3161 token over the canonical `ds:SignatureValue`) verifies against a configured TSA root, otherwise at "now";
- revocation from CRLs and OCSP responses the operator supplied (never fetched).

What it does NOT do, and says so: more than one signature in a document is reported unsupported (not guessed at); detached references to
external files are not resolved; `ArchiveTimeStamp` (long-term archive) chains are not evaluated; name and policy constraints are not
evaluated; and a valid seal says who sealed it, not that they had authority for any purpose.

Pure: bytes in, findings out. No network, no files.
"""

from __future__ import annotations

import base64
import hashlib
from collections.abc import Sequence
from datetime import UTC, datetime

from cryptography import x509
from cryptography.hazmat.primitives.serialization import Encoding
from cryptography.x509 import ocsp
from defusedxml import DefusedXmlException
from defusedxml import ElementTree as SafeET
from lxml import etree
from signxml import (  # type: ignore[attr-defined]
    InvalidDigest,
    InvalidInput,
    InvalidSignature,
    SignatureConfiguration,
    XMLVerifier,
)

from zid_platform.modules.evidence.application import cms, dsig

DS = "{http://www.w3.org/2000/09/xmldsig#}"
XADES_NS = ("http://uri.etsi.org/01903/v1.3.2#", "http://uri.etsi.org/01903/v1.4.1#")
SIGNED_PROPERTIES_TYPE = "http://uri.etsi.org/01903#SignedProperties"
C14N_INCLUSIVE = "http://www.w3.org/TR/2001/REC-xml-c14n-20010315"
C14N_INCLUSIVE_COMMENTS = C14N_INCLUSIVE + "#WithComments"
C14N_EXCLUSIVE = "http://www.w3.org/2001/10/xml-exc-c14n#"
C14N_EXCLUSIVE_COMMENTS = C14N_EXCLUSIVE + "WithComments"
_DIGESTS = {
    "http://www.w3.org/2001/04/xmlenc#sha256": "sha256",
    "http://www.w3.org/2001/04/xmldsig-more#sha384": "sha384",
    "http://www.w3.org/2001/04/xmlenc#sha512": "sha512",
}


def _local(el: etree._Element) -> str:
    return etree.QName(el).localname


def _xades(root: etree._Element, name: str) -> list[etree._Element]:
    return [
        el
        for el in root.iter()
        if isinstance(el.tag, str) and _local(el) == name and etree.QName(el).namespace in XADES_NS
    ]


def _certificates(sig: etree._Element) -> list[x509.Certificate]:
    out: list[x509.Certificate] = []
    for node in sig.iter(f"{DS}X509Certificate"):
        try:
            out.append(
                x509.load_der_x509_certificate(base64.b64decode("".join((node.text or "").split())))
            )
        except ValueError:
            continue
    return out


def _leaf(
    certs: Sequence[x509.Certificate], digest_match: x509.Certificate | None
) -> x509.Certificate | None:
    if digest_match is not None:
        return digest_match
    # no XAdES pointer: the certificate that does not issue any other one in the set is the signer's
    for c in certs:
        if not any(o is not c and o.issuer == c.subject and o.subject != c.subject for o in certs):
            return c
    return certs[0] if certs else None


def _signing_certificate_match(
    sig: etree._Element, certs: Sequence[x509.Certificate]
) -> tuple[x509.Certificate | None, bool]:
    """(the certificate THIS signature's signed properties name, whether a pointer exists). A pointer whose digest matches NO carried
    certificate is returned as (None, True): the signature then names a certificate it does not carry."""
    pointers = _xades(sig, "Cert")
    if not pointers:
        return None, False
    cert_digest = pointers[0].find("{*}CertDigest")
    if cert_digest is None:
        return None, True
    method = cert_digest.find(f"{DS}DigestMethod")
    value = cert_digest.findtext(f"{DS}DigestValue")
    alg = _DIGESTS.get(method.get("Algorithm", "") if method is not None else "")
    if alg is None or not value:
        return None, True
    try:
        expected = base64.b64decode("".join(value.split()))
    except ValueError:
        return None, True
    for c in certs:
        if hashlib.new(alg, c.public_bytes(Encoding.DER)).digest() == expected:
            return c, True
    return None, True


def _claimed_time(sig: etree._Element) -> str | None:
    for el in _xades(sig, "SigningTime"):
        return (el.text or "").strip() or None
    return None


def _c14n(el: etree._Element, algorithm: str) -> bytes:
    exclusive = algorithm in {C14N_EXCLUSIVE, C14N_EXCLUSIVE_COMMENTS}
    comments = algorithm in {C14N_INCLUSIVE_COMMENTS, C14N_EXCLUSIVE_COMMENTS}
    return etree.tostring(el, method="c14n", exclusive=exclusive, with_comments=comments)


def _timestamp_token(sig: etree._Element) -> tuple[bytes | None, str]:
    for stamp in _xades(sig, "SignatureTimeStamp"):
        method = stamp.find(f"{DS}CanonicalizationMethod")
        alg = method.get("Algorithm", C14N_INCLUSIVE) if method is not None else C14N_INCLUSIVE
        for enc in stamp.iter():
            if isinstance(enc.tag, str) and _local(enc) == "EncapsulatedTimeStamp":
                try:
                    return base64.b64decode("".join((enc.text or "").split())), alg
                except ValueError:
                    return None, alg
    return None, C14N_INCLUSIVE


def _embedded(
    sig: etree._Element,
) -> tuple[list[x509.Certificate], list[x509.CertificateRevocationList], list[ocsp.OCSPResponse]]:
    """The certificates, CRLs and OCSP responses a XAdES signature carries inside itself (CertificateValues, RevocationValues).
    Anything that does not parse is skipped. They are evidence like any other: used only after they pass the same checks, and they can
    never add a trust anchor."""
    certs: list[x509.Certificate] = []
    crls: list[x509.CertificateRevocationList] = []
    responses: list[ocsp.OCSPResponse] = []

    def raw(el: etree._Element) -> bytes | None:
        try:
            return base64.b64decode("".join((el.text or "").split()), validate=True)
        except ValueError:
            return None

    for el in sig.iter():
        if not isinstance(el.tag, str):
            continue
        name = _local(el)
        if name not in {
            "EncapsulatedX509Certificate",
            "EncapsulatedCRLValue",
            "EncapsulatedOCSPValue",
        }:
            continue
        data = raw(el)
        if data is None:
            continue
        try:
            if name == "EncapsulatedX509Certificate":
                certs.append(x509.load_der_x509_certificate(data))
            elif name == "EncapsulatedCRLValue":
                crls.append(x509.load_der_x509_crl(data))
            else:
                responses.append(ocsp.load_der_ocsp_response(data))
        except ValueError:
            continue
    return certs, crls, responses


def _parse(xml: bytes) -> etree._Element | None:
    try:
        SafeET.fromstring(xml, forbid_dtd=True, forbid_entities=True, forbid_external=True)
        return etree.fromstring(
            xml, parser=etree.XMLParser(resolve_entities=False, no_network=True)
        )
    except (DefusedXmlException, etree.XMLSyntaxError, ValueError, SafeET.ParseError):
        return None


def _signxml_agrees(xml: bytes, cert: x509.Certificate, at: datetime) -> bool | None:
    """The independent library's verdict for the FIRST signature (the only one it ever examines): True, False, or None if it refused
    to run. Used only as a cross-check on the engine in `dsig`: both must agree for the signature to count."""
    try:
        XMLVerifier().verify(
            xml,
            x509_cert=cert,
            expect_config=SignatureConfiguration(
                require_x509=True,
                location=".//",
                expect_references=True,
                verification_time=at,  # judge the certificate when the signature was made, as the engine does
            ),
        )
    except (InvalidDigest, InvalidSignature, InvalidInput):
        return False
    except Exception:  # noqa: BLE001 - any other refusal by the library means it could not vouch for the signature
        return None
    return True


def _inspect_signature(
    xml: bytes,
    root: etree._Element,
    index: int,
    sig: etree._Element,
    *,
    anchors: Sequence[x509.Certificate],
    when: datetime,
    tsa_roots: Sequence[x509.Certificate],
    crls: Sequence[x509.CertificateRevocationList],
    ocsp_responses: Sequence[ocsp.OCSPResponse],
) -> cms.Finding:
    problems: list[str] = []
    certs = _certificates(sig)
    if not certs:
        return cms.Finding(
            index, claimed_signing_time=_claimed_time(sig), problems=("SIGNER_CERTIFICATE_MISSING",)
        )
    emb_certs, emb_crls, emb_ocsp = _embedded(sig)
    named, pointer = _signing_certificate_match(sig, certs)
    if pointer and named is None:
        problems.append("SIGNING_CERTIFICATE_DIGEST_MISMATCH")
    cert = _leaf(certs, named)
    assert cert is not None  # certs is not empty

    result = dsig.verify(root, index, cert)
    problems.extend(result.problems)
    digest_ok, signature_ok, algorithm_ok = (
        result.digest_ok,
        result.signature_ok,
        result.algorithm_ok,
    )
    covers = result.covers_document
    if not covers:
        problems.append("REFERENCE_DOES_NOT_COVER_DOCUMENT")
    # XAdES: the signed properties must be inside THIS signature and be among what it signs, or the claimed time and signing
    # certificate could be swapped without breaking it
    props = _xades(sig, "SignedProperties")
    if props and (props[0].get("Id") or "") not in result.covered_ids:
        problems.append("SIGNED_PROPERTIES_NOT_SIGNED")
        signature_ok = False
    if pointer and named is None:
        signature_ok = False
    token, c14n_alg = _timestamp_token(sig)
    ts_state, ts_time = "NONE", None
    if token is not None:
        value = sig.find(f"{DS}SignatureValue")
        ts_state, ts_time = cms.trusted_time_from_token(
            token, _c14n(value, c14n_alg) if value is not None else b"", tsa_roots, when
        )
    judged_at = ts_time or when
    # the independent library must agree about the first signature; disagreement means it cannot be relied on
    if (
        index == 0
        and digest_ok
        and signature_ok
        and algorithm_ok
        and not problems_blocking(problems)
    ):
        verdict = _signxml_agrees(xml, cert, judged_at)
        if verdict is False:
            problems.append("VERIFIER_DISAGREEMENT")
            signature_ok = False

    chain, in_chain, anchor = cms.build_chain(cert, [*certs, *emb_certs], anchors, judged_at)
    revocation = (
        cms.check_revocation(in_chain, crls, judged_at, when, ocsp_responses)
        if chain is cms.Chain.TRUSTED
        else "NOT_CHECKED"
    )
    revocation, source = cms.merge_embedded_revocation(
        revocation,
        chain_trusted=chain is cms.Chain.TRUSTED,
        ts_state=ts_state,
        in_chain=in_chain,
        judged_at=judged_at,
        embedded_crls=emb_crls,
        embedded_ocsp=emb_ocsp,
    )
    if revocation == "REVOKED":
        problems.append("CERTIFICATE_REVOKED")
    return cms.Finding(
        index=index,
        covers_whole_file=covers,
        digest_ok=digest_ok,
        signature_ok=signature_ok,
        algorithm_ok=algorithm_ok,
        chain=chain,
        signer=cert.subject.rfc4514_string(),
        signer_serial=format(cert.serial_number, "x"),
        issuer=cert.issuer.rfc4514_string(),
        claimed_signing_time=_claimed_time(sig),
        timestamp_token_present=token is not None,
        timestamp=ts_state,
        trusted_time=ts_time.isoformat() if ts_time else None,
        revocation=revocation,
        revocation_source=source,
        anchor=anchor,
        problems=tuple(dict.fromkeys(problems)),
        covered_ids=result.covered_ids,
        chain_certificates=in_chain,
    )


_BLOCKING = {"REFERENCE_DOES_NOT_COVER_DOCUMENT", "SIGNED_PROPERTIES_NOT_SIGNED"}


def problems_blocking(problems: Sequence[str]) -> bool:
    """Problems after which asking the second library is pointless (it would only repeat them)."""
    return any(p in _BLOCKING or p.startswith("UNSUPPORTED") for p in problems)


def inspect_xml(
    xml: bytes,
    *,
    anchors: Sequence[x509.Certificate] = (),
    at: datetime | None = None,
    tsa_roots: Sequence[x509.Certificate] = (),
    crls: Sequence[x509.CertificateRevocationList] = (),
    ocsp_responses: Sequence[ocsp.OCSPResponse] = (),
) -> list[cms.Finding]:
    """One finding per signature an XML document carries, in document order; none if it carries none. Each signature is judged on its own
    terms (its own certificate, its own signed properties, its own time); `signature_result` then takes the weakest."""
    when = at or datetime.now(UTC)
    root = _parse(xml)
    if root is None:
        return [cms.Finding(0, problems=("NOT_XML_OR_UNSAFE",))]
    sigs = dsig.signatures(root)
    return [
        _inspect_signature(
            xml,
            root,
            i,
            sig,
            anchors=anchors,
            when=when,
            tsa_roots=tsa_roots,
            crls=crls,
            ocsp_responses=ocsp_responses,
        )
        for i, sig in enumerate(sigs)
    ]
