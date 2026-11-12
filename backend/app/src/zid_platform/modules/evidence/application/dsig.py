"""A small, strict XML-DSig verifier for documents that carry more than one signature (DTV-02; CR-073 A3).

Why this exists: the signxml library verifies the FIRST signature it finds and ignores the others, so a document with two signatures
cannot be checked with it one at a time. This module verifies a chosen signature, by position, under rules written to defeat signature
wrapping, the classic XML failure where the bytes that were signed are not the bytes that are used:

- a `ds:Reference` resolves only to the whole document (`URI=""`) or to ONE element by `#id`; an id that occurs more than once in the
  document is refused (`DUPLICATE_ID`), as is an id that is not there; external URIs are refused;
- only these transforms are accepted: enveloped-signature, and canonicalization (inclusive 1.0, exclusive, with or without comments).
  XPath, XSLT, base64 and anything else are refused (`UNSUPPORTED_TRANSFORM`): they are attack surface and XAdES does not need them;
- the enveloped transform removes the signature being verified and nothing else, so another signature in the document is part of the
  signed bytes of the first, exactly as the standard says;
- the digest of every reference and the signature value over the canonical `SignedInfo` must both verify; SHA-1 and unknown algorithms
  are refused, RSA is PKCS#1 v1.5 and ECDSA is the fixed-width r||s form;
- the signature must carry at least one reference, and the result says which ids were covered so the caller can require the XAdES
  `SignedProperties` to be among them.

Pure: an lxml tree and a certificate in, a verdict out. No network, no files. The caller has already refused DTDs, entities and external
references before the tree was built.
"""

from __future__ import annotations

import base64
import binascii
import copy
from dataclasses import dataclass, field

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from lxml import etree

DS = "{http://www.w3.org/2000/09/xmldsig#}"
ENVELOPED = "http://www.w3.org/2000/09/xmldsig#enveloped-signature"
C14N_INCLUSIVE = "http://www.w3.org/TR/2001/REC-xml-c14n-20010315"
C14N_EXCLUSIVE = "http://www.w3.org/2001/10/xml-exc-c14n#"
# canonicalization algorithm -> (exclusive, with_comments)
_C14N = {
    C14N_INCLUSIVE: (False, False),
    C14N_INCLUSIVE + "#WithComments": (False, True),
    C14N_EXCLUSIVE: (True, False),
    C14N_EXCLUSIVE + "WithComments": (True, True),
}
_DIGESTS: dict[str, type[hashes.HashAlgorithm]] = {
    "http://www.w3.org/2001/04/xmlenc#sha256": hashes.SHA256,
    "http://www.w3.org/2001/04/xmldsig-more#sha384": hashes.SHA384,
    "http://www.w3.org/2001/04/xmlenc#sha512": hashes.SHA512,
}
_SIGNATURES: dict[str, tuple[str, type[hashes.HashAlgorithm]]] = {
    "http://www.w3.org/2001/04/xmldsig-more#rsa-sha256": ("rsa", hashes.SHA256),
    "http://www.w3.org/2001/04/xmldsig-more#rsa-sha384": ("rsa", hashes.SHA384),
    "http://www.w3.org/2001/04/xmldsig-more#rsa-sha512": ("rsa", hashes.SHA512),
    "http://www.w3.org/2001/04/xmldsig-more#ecdsa-sha256": ("ecdsa", hashes.SHA256),
    "http://www.w3.org/2001/04/xmldsig-more#ecdsa-sha384": ("ecdsa", hashes.SHA384),
    "http://www.w3.org/2001/04/xmldsig-more#ecdsa-sha512": ("ecdsa", hashes.SHA512),
}
_ID_ATTRIBUTES = ("Id", "ID", "id", "{http://www.w3.org/XML/1998/namespace}id")


@dataclass(frozen=True, slots=True)
class DsigResult:
    digest_ok: bool = False
    signature_ok: bool = False
    algorithm_ok: bool = True
    covers_document: bool = False
    covered_ids: tuple[str, ...] = ()
    problems: tuple[str, ...] = field(default=())


def signatures(root: etree._Element) -> list[etree._Element]:
    """Every ds:Signature in document order. A signature nested inside another signature's Object is not a document signature."""
    found = [s for s in root.iter(f"{DS}Signature")]
    return [s for s in found if not any(a.tag == f"{DS}Signature" for a in s.iterancestors())]


def _c14n(node: etree._Element, algorithm: str) -> bytes | None:
    mode = _C14N.get(algorithm)
    if mode is None:
        return None
    return etree.tostring(node, method="c14n", exclusive=mode[0], with_comments=mode[1])


def _ids(root: etree._Element) -> dict[str, list[etree._Element]]:
    out: dict[str, list[etree._Element]] = {}
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        for attr in _ID_ATTRIBUTES:
            value = el.get(attr)
            if value:
                out.setdefault(value, []).append(el)
    return out


def _remove_keeping_tail(el: etree._Element) -> None:
    """Remove `el` as the enveloped transform does: the text after it belongs to the parent and stays."""
    parent = el.getparent()
    if parent is None:
        return
    tail = el.tail
    previous = el.getprevious()
    parent.remove(el)
    if tail:
        if previous is not None:
            previous.tail = (previous.tail or "") + tail
        else:
            parent.text = (parent.text or "") + tail


def _hash(data: bytes, cls: type[hashes.HashAlgorithm]) -> bytes:
    h = hashes.Hash(cls())
    h.update(data)
    return h.finalize()


def _b64(text: str | None) -> bytes | None:
    try:
        return base64.b64decode("".join((text or "").split()), validate=True)
    except (binascii.Error, ValueError):
        return None


def verify(root: etree._Element, index: int, cert: x509.Certificate) -> DsigResult:
    """Verify the `index`-th signature of the document against `cert`'s key. Every refusal is a named problem, never an exception."""
    sigs = signatures(root)
    if not 0 <= index < len(sigs):
        return DsigResult(problems=("SIGNATURE_NOT_FOUND",))
    sig = sigs[index]
    info = sig.find(f"{DS}SignedInfo")
    if info is None:
        return DsigResult(problems=("SIGNED_INFO_MISSING",))
    cm = info.find(f"{DS}CanonicalizationMethod")
    sm = info.find(f"{DS}SignatureMethod")
    references = info.findall(f"{DS}Reference")
    if cm is None or sm is None:
        return DsigResult(problems=("SIGNED_INFO_INCOMPLETE",))
    if not references:
        return DsigResult(problems=("NO_REFERENCES",))
    sig_alg = _SIGNATURES.get(sm.get("Algorithm", ""))
    info_c14n = _c14n(info, cm.get("Algorithm", ""))
    if sig_alg is None or info_c14n is None:
        return DsigResult(algorithm_ok=False, problems=("UNSUPPORTED_SIGNATURE_ALGORITHM",))

    problems: list[str] = []
    algorithm_ok = True
    digest_ok = True
    covers = False
    covered: list[str] = []
    for ref in references:
        uri = ref.get("URI")
        if uri is None or (uri != "" and not uri.startswith("#")):
            algorithm_ok = False
            problems.append("UNSUPPORTED_REFERENCE_URI")
            continue
        dm = ref.find(f"{DS}DigestMethod")
        dv = _b64(ref.findtext(f"{DS}DigestValue"))
        digest_cls = _DIGESTS.get(dm.get("Algorithm", "") if dm is not None else "")
        if digest_cls is None:
            algorithm_ok = False
            problems.append("UNSUPPORTED_DIGEST")
            continue
        transforms = [t.get("Algorithm", "") for t in ref.iterfind(f"{DS}Transforms/{DS}Transform")]
        c14n_alg = C14N_INCLUSIVE  # the default when no canonicalization is named
        enveloped = False
        refused = False
        for t in transforms:
            if t == ENVELOPED:
                enveloped = True
            elif t in _C14N:
                c14n_alg = t
            else:
                refused = True
        if refused:
            algorithm_ok = False
            problems.append("UNSUPPORTED_TRANSFORM")
            continue
        # resolve against a private copy so removing the signature (enveloped) cannot touch the caller's tree
        work = copy.deepcopy(root)
        work_sig = signatures(work)[index]
        target: etree._Element | None
        if uri == "":
            target = work
            covers = True
        else:
            wanted = uri[1:]
            matches = _ids(work).get(wanted, [])
            if len(matches) > 1:
                digest_ok = False
                problems.append("DUPLICATE_ID")
                continue
            if not matches:
                digest_ok = False
                problems.append("REFERENCE_TARGET_NOT_FOUND")
                continue
            target = matches[0]
            covered.append(wanted)
        if enveloped:
            _remove_keeping_tail(work_sig)
        payload = _c14n(target, c14n_alg)
        if payload is None or dv is None or _hash(payload, digest_cls) != dv:
            digest_ok = False
            problems.append("DOCUMENT_ALTERED_AFTER_SIGNING")

    value = _b64(sig.findtext(f"{DS}SignatureValue"))
    kind, hash_cls = sig_alg
    signature_ok = False
    key = cert.public_key()
    try:
        if value is None:
            raise InvalidSignature
        if kind == "rsa" and isinstance(key, rsa.RSAPublicKey):
            key.verify(value, info_c14n, padding.PKCS1v15(), hash_cls())
            signature_ok = True
        elif kind == "ecdsa" and isinstance(key, ec.EllipticCurvePublicKey):
            half = (key.curve.key_size + 7) // 8
            if len(value) != 2 * half:
                raise InvalidSignature
            der = encode_dss_signature(
                int.from_bytes(value[:half], "big"), int.from_bytes(value[half:], "big")
            )
            key.verify(der, info_c14n, ec.ECDSA(hash_cls()))
            signature_ok = True
        else:
            algorithm_ok = False
            problems.append("UNSUPPORTED_SIGNATURE_ALGORITHM")
    except InvalidSignature:
        problems.append("SIGNATURE_VALUE_INVALID")
    return DsigResult(
        digest_ok=digest_ok,
        signature_ok=signature_ok,
        algorithm_ok=algorithm_ok,
        covers_document=covers,
        covered_ids=tuple(covered),
        problems=tuple(dict.fromkeys(problems)),
    )
