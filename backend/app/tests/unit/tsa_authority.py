"""A small timestamp authority for tests: a CA (optionally with an intermediate), a TSA certificate and real CMS tokens.

Each keyword of `response` spoils exactly one property of an otherwise valid token, and `make_crl` issues revocation
lists, so a test can break one thing at a time.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, padding, rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from pyasn1.codec.der import decoder, encoder
from pyasn1.type import univ
from pyasn1_modules import rfc3161, rfc5035, rfc5280, rfc5652
from zid_evidence import Digest, hash_bytes
from zid_platform.modules.evidence.infrastructure.tsa import _tlv as _tlv_impl

SHA256 = "2.16.840.1.101.3.4.2.1"
# Relative to the real clock so nothing here ages out (a CLI test cannot inject a clock); the other tests pass `now=NOW`.
NOW = datetime.now(UTC).replace(microsecond=0)
DIGEST = hash_bytes(b"checkpoint manifest", alg="sha-256")


def _name(common: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common)])


@dataclass
class Authority:
    """A CA that may delegate to an intermediate, and the TSA certificate it issued."""

    root: x509.Certificate
    tsa_cert: x509.Certificate
    tsa_key: object
    chain: list[x509.Certificate]  # certificates the token carries (TSA first)
    root_key: object = None
    mid: x509.Certificate | None = None
    mid_key: object = None


def _cert(
    subject: str,
    key: object,
    issuer_name: x509.Name,
    issuer_key: object,
    *,
    ca: bool,
    eku: bool = False,
    eku_critical: bool = True,
    crl_url: str | None = None,
    not_before: datetime = NOW - timedelta(days=30),
    not_after: datetime = NOW + timedelta(days=300),
) -> x509.Certificate:
    builder = (
        x509.CertificateBuilder()
        .subject_name(_name(subject))
        .issuer_name(issuer_name)
        .public_key(key.public_key())  # type: ignore[attr-defined]
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_before)
        .not_valid_after(not_after)
        .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)  # type: ignore[attr-defined]
    )
    if ca:
        builder = builder.add_extension(
            x509.KeyUsage(False, False, False, False, False, True, True, False, False),
            critical=True,
        )
    if crl_url:
        builder = builder.add_extension(
            x509.CRLDistributionPoints(
                [
                    x509.DistributionPoint(
                        [x509.UniformResourceIdentifier(crl_url)], None, None, None
                    )
                ]
            ),
            critical=False,
        )
    if eku:
        builder = builder.add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.TIME_STAMPING]), critical=eku_critical
        )
    algorithm = None if isinstance(issuer_key, ed25519.Ed25519PrivateKey) else hashes.SHA256()
    return builder.sign(issuer_key, algorithm)  # type: ignore[arg-type]


def authority(
    tsa_key: object | None = None,
    *,
    intermediate: bool = False,
    mid_crl_url: str | None = None,
    **tsa_cert_kwargs: object,
) -> Authority:
    root_key = ec.generate_private_key(ec.SECP256R1())
    root = _cert("Test Root", root_key, _name("Test Root"), root_key, ca=True)
    tsa_key = tsa_key or ec.generate_private_key(ec.SECP256R1())
    issuer, issuer_key, chain_tail = root, root_key, []
    mid = mid_key = None
    if intermediate:
        mid_key = ec.generate_private_key(ec.SECP256R1())
        mid = _cert(
            "Test Intermediate", mid_key, root.subject, root_key, ca=True, crl_url=mid_crl_url
        )
        issuer, issuer_key, chain_tail = mid, mid_key, [mid]
    tsa = _cert(
        "Test TSA",
        tsa_key,
        issuer.subject,
        issuer_key,
        ca=False,
        eku=True,
        **tsa_cert_kwargs,  # type: ignore[arg-type]
    )
    return Authority(
        root=root,
        tsa_cert=tsa,
        tsa_key=tsa_key,
        chain=[tsa, *chain_tail],
        root_key=root_key,
        mid=mid,
        mid_key=mid_key,
    )


def make_crl(
    issuer: x509.Certificate,
    issuer_key: object,
    revoked: list[tuple[x509.Certificate, datetime]] | None = None,
    *,
    last_update: datetime = NOW,
    next_update: datetime | None = None,
    invalidity: dict[int, datetime] | None = None,
    reason: x509.ReasonFlags | None = None,
) -> x509.CertificateRevocationList:
    """A CRL by `issuer` listing `revoked` (certificate, revocation date). `invalidity` maps a serial to an invalidity date."""
    builder = (
        x509.CertificateRevocationListBuilder()
        .issuer_name(issuer.subject)
        .last_update(last_update)
        .next_update(next_update or last_update + timedelta(days=7))
    )
    for cert, when in revoked or []:
        entry = (
            x509.RevokedCertificateBuilder().serial_number(cert.serial_number).revocation_date(when)
        )
        if reason is not None:
            entry = entry.add_extension(x509.CRLReason(reason), critical=False)
        if invalidity and cert.serial_number in invalidity:
            entry = entry.add_extension(
                x509.InvalidityDate(invalidity[cert.serial_number]), critical=False
            )
        builder = builder.add_revoked_certificate(entry.build())
    algorithm = None if isinstance(issuer_key, ed25519.Ed25519PrivateKey) else hashes.SHA256()
    return builder.sign(issuer_key, algorithm)  # type: ignore[arg-type]


def _any(value: object) -> univ.Any:
    return univ.Any(encoder.encode(value))


def _attribute(oid: str, value: object) -> rfc5652.Attribute:
    attr = rfc5652.Attribute()
    attr["attrType"] = univ.ObjectIdentifier(oid)
    attr["attrValues"][0] = _any(value)
    return attr


def _sign(key: object, data: bytes) -> bytes:
    if isinstance(key, ec.EllipticCurvePrivateKey):
        return key.sign(data, ec.ECDSA(hashes.SHA256()))
    if isinstance(key, rsa.RSAPrivateKey):
        return key.sign(data, padding.PKCS1v15(), hashes.SHA256())
    return key.sign(data)  # type: ignore[attr-defined]


def _signature_oid(key: object) -> str:
    if isinstance(key, ec.EllipticCurvePrivateKey):
        return "1.2.840.10045.4.3.2"
    if isinstance(key, rsa.RSAPrivateKey):
        return "1.2.840.113549.1.1.11"
    return "1.3.101.112"


def _tlv(tag: int, body: bytes) -> bytes:
    return _tlv_impl(tag, body)


def response(
    auth: Authority,
    digest: Digest = DIGEST,
    *,
    nonce: int | None = 1234,
    gen_time: datetime = NOW,
    sign_with: object | None = None,
    stamped_digest: Digest | None = None,
    include_signing_cert: bool = True,
    cert_hash_of: x509.Certificate | None = None,
    tamper_tst: bool = False,
) -> bytes:
    """A granted TimeStampResp. The keyword arguments each spoil exactly one property."""
    stamped = stamped_digest or digest
    tst = rfc3161.TSTInfo()
    tst["version"] = 1
    tst["policy"] = univ.ObjectIdentifier("1.2.3.4.5")
    tst["messageImprint"]["hashAlgorithm"]["algorithm"] = univ.ObjectIdentifier(SHA256)
    tst["messageImprint"]["hashedMessage"] = univ.OctetString(stamped.value)
    tst["serialNumber"] = 4242
    tst["genTime"] = gen_time.strftime("%Y%m%d%H%M%SZ")
    if nonce is not None:
        tst["nonce"] = nonce
    tst_der = encoder.encode(tst)

    signed = rfc5652.SignedData()
    signed["version"] = 3
    signed["digestAlgorithms"][0]["algorithm"] = univ.ObjectIdentifier(SHA256)
    signed["encapContentInfo"]["eContentType"] = rfc3161.id_ct_TSTInfo
    signed["encapContentInfo"]["eContent"] = tst_der
    for i, cert in enumerate(auth.chain):
        parsed, _ = decoder.decode(
            cert.public_bytes(serialization.Encoding.DER), asn1Spec=rfc5280.Certificate()
        )
        signed["certificates"][i]["certificate"] = parsed

    info = signed["signerInfos"][0]
    info["version"] = 1
    issuer, _ = decoder.decode(auth.tsa_cert.issuer.public_bytes(), asn1Spec=rfc5280.Name())
    info["sid"]["issuerAndSerialNumber"]["issuer"][0] = issuer[0]
    info["sid"]["issuerAndSerialNumber"]["serialNumber"] = auth.tsa_cert.serial_number
    info["digestAlgorithm"]["algorithm"] = univ.ObjectIdentifier(SHA256)

    signing_cert = rfc5035.SigningCertificateV2()
    ess = signing_cert["certs"][0]
    ess["hashAlgorithm"]["algorithm"] = univ.ObjectIdentifier(SHA256)
    named = cert_hash_of or auth.tsa_cert
    ess["certHash"] = univ.OctetString(
        hashlib.sha256(named.public_bytes(serialization.Encoding.DER)).digest()
    )
    attrs = [
        _attribute("1.2.840.113549.1.9.3", rfc3161.id_ct_TSTInfo),
        _attribute("1.2.840.113549.1.9.4", univ.OctetString(hashlib.sha256(tst_der).digest())),
    ]
    if include_signing_cert:
        attrs.append(_attribute("1.2.840.113549.1.9.16.2.47", signing_cert))
    for i, attr in enumerate(attrs):
        info["signedAttrs"][i] = attr

    key = sign_with or auth.tsa_key
    info["signatureAlgorithm"]["algorithm"] = univ.ObjectIdentifier(_signature_oid(key))
    info["signature"] = univ.OctetString(
        _sign(key, b"\x31" + encoder.encode(info["signedAttrs"])[1:])
    )
    signed["signerInfos"][0] = info

    if tamper_tst:  # change the stamped content after signing: the message digest no longer matches
        tst["serialNumber"] = (
            4243  # a field nothing else checks, so only the signed digest can catch it
        )
        signed["encapContentInfo"]["eContent"] = encoder.encode(tst)

    content = rfc5652.ContentInfo()
    content["contentType"] = rfc5652.id_signedData
    content["content"] = _any(signed)
    return _tlv(0x30, bytes.fromhex("3003020100") + encoder.encode(content))
