"""Offline validation of RFC 3161 timestamps (DEC-F2 §1, §3): what an anchored checkpoint proves, checkable by anyone.

Optional extra: `pip install zid-evidence[timestamps]` (adds pyasn1; `cryptography` is already required).

`verify_timestamp_response` accepts a response only when all of this holds, and raises `TimestampInvalid` (never returns a
partial answer) otherwise:

- the TSA granted the request (status 0 or 1) and returned a token;
- the token is CMS SignedData of content type id-ct-TSTInfo, with exactly one signer;
- the TSTInfo imprint is the digest we sent (same algorithm, same bytes) and, when we sent a nonce, it is echoed;
- the signed attributes carry the content type and a message digest equal to the hash of the TSTInfo, and the signature
  over them verifies under the signer certificate's key (RSA PKCS#1 v1.5, ECDSA or Ed25519; RSA below 2048 bits is refused);
- the signer certificate is the one the token names (ESSCertID, RFC 3161 §2.4.2) and carries only the critical
  id-kp-timeStamping extended key usage (RFC 3161 §2.3);
- a chain of certificates (those the token carries plus the configured roots) leads from the signer to a configured root,
  each issuer a CA, every certificate valid at the time the TSA says it stamped.

`check_revocation` is the second step, over the verified chain and the CRLs you hand it (it never fetches anything):
a certificate revoked at or before the stamp time (the CRL's invalidity date when it has one) fails the timestamp; one
revoked later does not, because the stamp was made while it was good, and the result says `revoked_later`. In
`required` mode every certificate below the root needs a CRL that is signed by its issuer, already published and not
expired; in `if_available` mode a missing CRL leaves the result `partial`. CRLs are periodic, so the one in force usually
predates the stamp: `revocation_as_of` is the oldest publication time relied on, and a revocation published after it is
not known. Delta and indirect CRLs and OCSP are not read. Signing time (`genTime`) is the TSA's claim, bounded by the trust you place in the roots you configure.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from cryptography import x509
from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, padding, rsa
from cryptography.hazmat.primitives.serialization import Encoding
from cryptography.x509.oid import ExtendedKeyUsageOID
from pyasn1.codec.der import decoder, encoder
from pyasn1.error import PyAsn1Error
from pyasn1.type import univ
from pyasn1_modules import rfc2634, rfc3161, rfc5035, rfc5652

from zid_evidence.digest import Digest

REF_PREFIX = "rfc3161:"
MAX_CHAIN = 8
CLOCK_SKEW = timedelta(minutes=5)
MIN_RSA_BITS = 2048

_IMPRINT_OIDS = {
    "sha-256": "2.16.840.1.101.3.4.2.1",
    "sha-384": "2.16.840.1.101.3.4.2.2",
    "sha-512": "2.16.840.1.101.3.4.2.3",
}
_HASHES: dict[str, type[hashes.HashAlgorithm]] = {
    "2.16.840.1.101.3.4.2.1": hashes.SHA256,
    "2.16.840.1.101.3.4.2.2": hashes.SHA384,
    "2.16.840.1.101.3.4.2.3": hashes.SHA512,
}
_RSA_SIGNATURES = {
    "1.2.840.113549.1.1.1",  # rsaEncryption
    "1.2.840.113549.1.1.11",  # sha256WithRSAEncryption
    "1.2.840.113549.1.1.12",
    "1.2.840.113549.1.1.13",
}
_EC_SIGNATURES = {
    "1.2.840.10045.2.1",  # id-ecPublicKey
    "1.2.840.10045.4.3.2",  # ecdsa-with-SHA256
    "1.2.840.10045.4.3.3",
    "1.2.840.10045.4.3.4",
}
_ED25519_SIGNATURE = "1.3.101.112"
_SIGNED_DATA = "1.2.840.113549.1.7.2"
_ATTR_CONTENT_TYPE = "1.2.840.113549.1.9.3"
_ATTR_MESSAGE_DIGEST = "1.2.840.113549.1.9.4"
_ATTR_SIGNING_CERT = "1.2.840.113549.1.9.16.2.12"
_ATTR_SIGNING_CERT_V2 = "1.2.840.113549.1.9.16.2.47"
_SHA256_OID = "2.16.840.1.101.3.4.2.1"
_HASH_BY_NAME = {
    "sha1": hashlib.sha1,
    "sha256": hashlib.sha256,
    "sha384": hashlib.sha384,
    "sha512": hashlib.sha512,
}


class TimestampInvalid(ValueError):
    """The answer does not prove a timestamp over the digest, under the trust list given."""


@dataclass(frozen=True, slots=True)
class VerifiedTimestamp:
    gen_time: datetime
    policy: str
    serial_number: int
    tsa_subject: str
    tsa_certificate_sha256: str
    chain: tuple[x509.Certificate, ...] = ()  # signer first, the configured root last
    revocation: str = "not_checked"  # not_checked | good | partial | revoked_later
    # the oldest CRL publication time relied on: revocations after it are not known to the check. A caller that needs the
    # stamp itself covered compares it with `gen_time`.
    revocation_as_of: datetime | None = None


def load_trust_roots(path: str | Path) -> tuple[x509.Certificate, ...]:
    """The configured trust list: one or more PEM certificates. An empty or unreadable file is an error, never "trust nothing"."""
    try:
        data = Path(path).read_bytes()
        roots = tuple(x509.load_pem_x509_certificates(data))
    except (OSError, ValueError) as e:
        raise TimestampInvalid(f"TSA trust list cannot be read: {type(e).__name__}") from e
    if not roots:
        raise TimestampInvalid("TSA trust list holds no certificates")
    return roots


def verify_timestamp_ref(
    ref: str, digest: Digest, roots: Sequence[x509.Certificate], *, now: datetime | None = None
) -> VerifiedTimestamp:
    """Re-verify a stored `rfc3161:<base64 DER response>` reference (no nonce: it was checked when the stamp was taken)."""
    if not ref.startswith(REF_PREFIX):
        raise TimestampInvalid("not an RFC 3161 reference")
    try:
        der = base64.b64decode(ref[len(REF_PREFIX) :], validate=True)
    except (binascii.Error, ValueError) as e:
        raise TimestampInvalid("timestamp reference is not base64") from e
    return verify_timestamp_response(der, digest, roots, now=now)


def verify_timestamp_response(
    der: bytes,
    digest: Digest,
    roots: Sequence[x509.Certificate],
    *,
    nonce: int | None = None,
    now: datetime | None = None,
) -> VerifiedTimestamp:
    if not roots:
        raise TimestampInvalid("no TSA trust list is configured")
    try:
        return _verify(der, digest, roots, nonce, now or datetime.now(UTC))
    except TimestampInvalid:
        raise
    except (
        PyAsn1Error,
        ValueError,
        TypeError,
        KeyError,
        IndexError,
        AttributeError,
        UnsupportedAlgorithm,
    ) as e:
        raise TimestampInvalid(f"timestamp response is malformed: {type(e).__name__}") from e


def _decode(substrate: Any, spec: Any) -> Any:
    value, rest = decoder.decode(bytes(substrate), asn1Spec=spec)
    if rest:
        raise TimestampInvalid("trailing bytes after an ASN.1 structure")
    return value


def _oid(value: Any) -> str:
    return str(value)


def _verify(
    der: bytes, digest: Digest, roots: Sequence[x509.Certificate], nonce: int | None, now: datetime
) -> VerifiedTimestamp:
    resp = _decode(der, rfc3161.TimeStampResp())
    status = int(resp["status"]["status"])
    if status not in (0, 1):
        raise TimestampInvalid(f"TSA did not grant the timestamp (status {status})")
    if not resp["timeStampToken"].hasValue():
        raise TimestampInvalid("granted response carries no token")

    content_info = resp["timeStampToken"]
    if _oid(content_info["contentType"]) != _SIGNED_DATA:
        raise TimestampInvalid("token is not CMS SignedData")
    signed = _decode(content_info["content"], rfc5652.SignedData())
    if _oid(signed["encapContentInfo"]["eContentType"]) != _oid(rfc3161.id_ct_TSTInfo):
        raise TimestampInvalid("token content is not TSTInfo")
    if len(signed["signerInfos"]) != 1:
        raise TimestampInvalid("token must have exactly one signer")
    econtent = signed["encapContentInfo"]["eContent"]
    if not econtent.hasValue():
        raise TimestampInvalid("token carries no TSTInfo")
    tst_bytes = bytes(econtent)
    tst = _decode(tst_bytes, rfc3161.TSTInfo())

    # --- what was stamped
    if int(tst["version"]) != 1:
        raise TimestampInvalid("unsupported TSTInfo version")
    imprint = tst["messageImprint"]
    if _oid(imprint["hashAlgorithm"]["algorithm"]) != _IMPRINT_OIDS[digest.alg]:
        raise TimestampInvalid("timestamp covers a different hash algorithm")
    if bytes(imprint["hashedMessage"]) != digest.value:
        raise TimestampInvalid("timestamp covers a different digest")
    if nonce is not None and not (tst["nonce"].hasValue() and int(tst["nonce"]) == nonce):
        raise TimestampInvalid("timestamp does not echo our nonce")
    gen_time = tst["genTime"].asDateTime
    if gen_time.tzinfo is None:
        gen_time = gen_time.replace(tzinfo=UTC)
    if gen_time > now + CLOCK_SKEW:
        raise TimestampInvalid("timestamp is dated in the future")

    # --- who signed, and did they sign this
    certs = _embedded_certificates(signed)
    info = signed["signerInfos"][0]
    signer = _find_signer(info["sid"], certs)
    _check_signed_attributes(info, tst_bytes)
    _check_signing_certificate(info, signer)
    _check_signature(info, signer)
    _check_signer_certificate(signer)

    # --- and are they trusted
    chain = _check_chain(signer, certs, roots, gen_time)

    return VerifiedTimestamp(
        gen_time=gen_time,
        policy=_oid(tst["policy"]),
        serial_number=int(tst["serialNumber"]),
        tsa_subject=signer.subject.rfc4514_string(),
        tsa_certificate_sha256=signer.fingerprint(hashes.SHA256()).hex(),
        chain=chain,
    )


def _embedded_certificates(signed: Any) -> list[x509.Certificate]:
    certs: list[x509.Certificate] = []
    if signed["certificates"].hasValue():
        for choice in signed["certificates"]:
            if choice.getName() == "certificate":
                certs.append(x509.load_der_x509_certificate(encoder.encode(choice["certificate"])))
    return certs


def _find_signer(sid: Any, certs: list[x509.Certificate]) -> x509.Certificate:
    kind = sid.getName()
    for cert in certs:
        if kind == "issuerAndSerialNumber":
            ias = sid["issuerAndSerialNumber"]
            if cert.serial_number == int(
                ias["serialNumber"]
            ) and cert.issuer.public_bytes() == encoder.encode(ias["issuer"]):
                return cert
        else:
            try:
                ski = cert.extensions.get_extension_for_class(
                    x509.SubjectKeyIdentifier
                ).value.digest
            except x509.ExtensionNotFound:
                continue
            if ski == bytes(sid["subjectKeyIdentifier"]):
                return cert
    raise TimestampInvalid("the signer's certificate is not in the token")


def _hash_of(oid: Any) -> hashes.HashAlgorithm:
    cls = _HASHES.get(_oid(oid))
    if cls is None:
        raise TimestampInvalid("unsupported digest algorithm in the token")
    return cls()


def _attributes(info: Any) -> dict[str, list[Any]]:
    if not info["signedAttrs"].hasValue():
        raise TimestampInvalid("token has no signed attributes")
    found: dict[str, list[Any]] = {}
    for attr in info["signedAttrs"]:
        found.setdefault(_oid(attr["attrType"]), []).extend(list(attr["attrValues"]))
    return found


def _check_signed_attributes(info: Any, tst_bytes: bytes) -> None:
    attrs = _attributes(info)
    for required in (_ATTR_CONTENT_TYPE, _ATTR_MESSAGE_DIGEST):
        if len(attrs.get(required, [])) != 1:
            raise TimestampInvalid("signed attributes are incomplete")
    content_type = _decode(attrs[_ATTR_CONTENT_TYPE][0], univ.ObjectIdentifier())
    if _oid(content_type) != _oid(rfc3161.id_ct_TSTInfo):
        raise TimestampInvalid("signed content type is not TSTInfo")
    message_digest = bytes(_decode(attrs[_ATTR_MESSAGE_DIGEST][0], univ.OctetString()))
    hasher = _hash_of(info["digestAlgorithm"]["algorithm"])
    h = hashes.Hash(hasher)
    h.update(tst_bytes)
    if h.finalize() != message_digest:
        raise TimestampInvalid("message digest does not match the TSTInfo")


def _check_signing_certificate(info: Any, signer: x509.Certificate) -> None:
    """RFC 3161 §2.4.2: the token must name its signer's certificate (ESSCertID or ESSCertIDv2), and it must be the one used."""
    attrs = _attributes(info)
    der = signer.public_bytes(Encoding.DER)
    if _ATTR_SIGNING_CERT_V2 in attrs:
        sc = _decode(attrs[_ATTR_SIGNING_CERT_V2][0], rfc5035.SigningCertificateV2())
        first = sc["certs"][0]
        alg = (
            _oid(first["hashAlgorithm"]["algorithm"])
            if first["hashAlgorithm"].hasValue()
            else _SHA256_OID
        )
        name = {
            "2.16.840.1.101.3.4.2.1": "sha256",
            "2.16.840.1.101.3.4.2.2": "sha384",
            "2.16.840.1.101.3.4.2.3": "sha512",
        }.get(alg)
        if name is None:
            raise TimestampInvalid("unsupported hash in the signing certificate attribute")
        expected = _HASH_BY_NAME[name](der).digest()
        cert_hash = bytes(first["certHash"])
    elif _ATTR_SIGNING_CERT in attrs:
        sc1 = _decode(attrs[_ATTR_SIGNING_CERT][0], rfc2634.SigningCertificate())
        expected = hashlib.sha1(der, usedforsecurity=False).digest()
        cert_hash = bytes(sc1["certs"][0]["certHash"])
    else:
        raise TimestampInvalid("token does not name its signing certificate")
    if expected != cert_hash:
        raise TimestampInvalid("signing certificate attribute does not match the signer")


def _check_signature(info: Any, signer: x509.Certificate) -> None:
    algorithm = _oid(info["signatureAlgorithm"]["algorithm"])
    hasher = _hash_of(info["digestAlgorithm"]["algorithm"])
    signature = bytes(info["signature"])
    # The signature is over the DER of the SET OF attributes, with the universal SET tag in place of the implicit [0].
    signed_attrs = b"\x31" + encoder.encode(info["signedAttrs"])[1:]
    key = signer.public_key()
    try:
        if isinstance(key, rsa.RSAPublicKey):
            if key.key_size < MIN_RSA_BITS or algorithm not in _RSA_SIGNATURES:
                raise TimestampInvalid("signer key or signature algorithm is not acceptable")
            key.verify(signature, signed_attrs, padding.PKCS1v15(), hasher)
        elif isinstance(key, ec.EllipticCurvePublicKey):
            if algorithm not in _EC_SIGNATURES:
                raise TimestampInvalid("signer key or signature algorithm is not acceptable")
            key.verify(signature, signed_attrs, ec.ECDSA(hasher))
        elif isinstance(key, ed25519.Ed25519PublicKey):
            if algorithm != _ED25519_SIGNATURE:
                raise TimestampInvalid("signer key or signature algorithm is not acceptable")
            key.verify(signature, signed_attrs)
        else:
            raise TimestampInvalid("signer key type is not accepted")
    except InvalidSignature as e:
        raise TimestampInvalid("token signature does not verify") from e


def _check_signer_certificate(cert: x509.Certificate) -> None:
    try:
        eku = cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage)
    except x509.ExtensionNotFound as e:
        raise TimestampInvalid("TSA certificate has no extended key usage") from e
    if not eku.critical or list(eku.value) != [ExtendedKeyUsageOID.TIME_STAMPING]:
        raise TimestampInvalid("TSA certificate must carry only a critical id-kp-timeStamping")


def _valid_at(cert: x509.Certificate, at: datetime) -> bool:
    return cert.not_valid_before_utc <= at <= cert.not_valid_after_utc


def _is_ca(cert: x509.Certificate) -> bool:
    try:
        if not cert.extensions.get_extension_for_class(x509.BasicConstraints).value.ca:
            return False
    except x509.ExtensionNotFound:
        return False
    try:
        usage = cert.extensions.get_extension_for_class(x509.KeyUsage).value
    except x509.ExtensionNotFound:
        return True
    return usage.key_cert_sign


def _issued_by(child: x509.Certificate, parent: x509.Certificate) -> bool:
    try:
        child.verify_directly_issued_by(parent)
    except (ValueError, TypeError, InvalidSignature, UnsupportedAlgorithm):
        return False
    return True


def _check_chain(
    signer: x509.Certificate,
    pool: list[x509.Certificate],
    roots: Sequence[x509.Certificate],
    at: datetime,
) -> tuple[x509.Certificate, ...]:
    current = signer
    path = [signer]
    visited: set[bytes] = set()
    for _ in range(MAX_CHAIN):
        fingerprint = current.fingerprint(hashes.SHA256())
        if fingerprint in visited:
            break
        visited.add(fingerprint)
        if not _valid_at(current, at):
            raise TimestampInvalid(
                "a certificate in the TSA chain was not valid when the timestamp was made"
            )
        for root in roots:
            if root == current:
                return tuple(path)
            if _issued_by(current, root):
                if not _is_ca(root) or not _valid_at(root, at):
                    raise TimestampInvalid(
                        "the configured root is not a valid CA for this timestamp"
                    )
                return (*path, root)
        parent = next(
            (p for p in pool if p != current and _is_ca(p) and _issued_by(current, p)), None
        )
        if parent is None:
            break
        current = parent
        path.append(parent)
    raise TimestampInvalid("TSA certificate does not chain to a configured root")


# ------------------------------------------------------------------------------------------------ revocation

Revocation = Literal["off", "if_available", "required"]


def crl_urls(cert: x509.Certificate) -> list[str]:
    """The http(s) CRL distribution points a certificate names. Call it only for certificates already verified."""
    try:
        points = cert.extensions.get_extension_for_class(x509.CRLDistributionPoints).value
    except x509.ExtensionNotFound:
        return []
    urls: list[str] = []
    for point in points:
        for name in point.full_name or ():
            if isinstance(name, x509.UniformResourceIdentifier) and name.value.lower().startswith(
                ("http://", "https://")
            ):
                urls.append(name.value)
    return urls


def load_crls(data: bytes) -> tuple[x509.CertificateRevocationList, ...]:
    """One or more CRLs: PEM (several allowed) or a single DER."""
    if b"-----BEGIN X509 CRL-----" in data:
        blocks = re.findall(rb"-----BEGIN X509 CRL-----.+?-----END X509 CRL-----", data, re.DOTALL)
        return tuple(x509.load_pem_x509_crl(b) for b in blocks)
    return (x509.load_der_x509_crl(data),)


def _crl_covers(
    crl: x509.CertificateRevocationList,
    cert: x509.Certificate,
    issuer: x509.Certificate,
    now: datetime,
) -> bool:
    """A CRL that speaks for `cert`: from its issuer, signed by it, already published and not yet expired.

    Real CRLs are periodic, so the one in force usually predates the stamp; the result records how old it is."""
    if crl.issuer != cert.issuer:
        return False
    try:
        if not issuer.extensions.get_extension_for_class(x509.KeyUsage).value.crl_sign:
            return False
    except x509.ExtensionNotFound:
        pass
    if not crl.is_signature_valid(issuer.public_key()):  # type: ignore[arg-type]  # cryptography widens public_key()
        return False
    if crl.last_update_utc > now + CLOCK_SKEW:
        return False  # dated in the future
    return crl.next_update_utc is None or crl.next_update_utc >= now


def check_revocation(
    verified: VerifiedTimestamp,
    crls: Sequence[x509.CertificateRevocationList],
    *,
    mode: Revocation = "if_available",
    now: datetime | None = None,
) -> VerifiedTimestamp:
    """The verified timestamp with its revocation state, or `TimestampInvalid` when a certificate was revoked by the stamp
    time (or, in `required` mode, when a certificate has no current CRL)."""
    if mode == "off":
        return verified
    now = now or datetime.now(UTC)
    state = "good"
    as_of: datetime | None = None
    chain = verified.chain
    for i, cert in enumerate(
        chain[:-1]
    ):  # the configured root is the trust anchor and is not checked
        issuer = chain[i + 1]
        usable = [c for c in crls if _crl_covers(c, cert, issuer, now)]
        if not usable:
            if mode == "required":
                raise TimestampInvalid(
                    f"no current revocation list for {cert.subject.rfc4514_string()}"
                )
            state = "partial" if state == "good" else state
            continue
        for crl in usable:
            as_of = crl.last_update_utc if as_of is None else min(as_of, crl.last_update_utc)
            entry = crl.get_revoked_certificate_by_serial_number(cert.serial_number)
            if entry is None:
                continue
            effective = entry.revocation_date_utc
            try:
                effective = entry.extensions.get_extension_for_class(
                    x509.InvalidityDate
                ).value.invalidity_date_utc
            except x509.ExtensionNotFound:
                pass
            if effective <= verified.gen_time:
                raise TimestampInvalid(
                    f"{cert.subject.rfc4514_string()} was revoked at {effective.isoformat()}, before the timestamp"
                )
            state = "revoked_later"
    return replace(verified, revocation=state, revocation_as_of=as_of)
