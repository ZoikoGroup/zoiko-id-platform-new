"""Test fixtures for passport chip checks: a country signing CA, a document signer, and a real EF.SOD signed by it, built by hand in DER.

Nothing is mocked. The SOD is a genuine CMS SignedData whose content is a genuine LDS Security Object over genuine data-group bytes, signed with a real
key, so each rule in the verifier is exercised by changing exactly one property of a good chip.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import timedelta

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding
from pdf_signing import NOW, Party, _oid_der, der, ec_key, make_cert

OID_SIGNED_DATA = "1.2.840.113549.1.7.2"
OID_LDS = "2.23.136.1.1.1"
OID_DATA = "1.2.840.113549.1.7.1"
OID_CONTENT_TYPE = "1.2.840.113549.1.9.3"
OID_MESSAGE_DIGEST = "1.2.840.113549.1.9.4"
OID_ECDSA_SHA256 = "1.2.840.10045.4.3.2"
OID_SHA1 = "1.3.14.3.2.26"
OID = {
    "sha256": "2.16.840.1.101.3.4.2.1",
    "sha384": "2.16.840.1.101.3.4.2.2",
    "sha512": "2.16.840.1.101.3.4.2.3",
}


def integer(n: int) -> bytes:
    body = n.to_bytes(max(1, (n.bit_length() + 8) // 8), "big")
    return der(0x02, body)


def mrz_td3(
    *,
    surname="ERIKSSON",
    given="ANNA MARIA",
    number="L898902C3",
    nationality="UTO",
    birth="740812",
    sex="F",
    expiry="320415",
    state="UTO",
) -> str:
    """A genuine two-line TD3 machine-readable zone with correct ICAO check digits (the specification's own worked example by default)."""
    from zid_platform.modules.evidence.domain.mrz import check_digit

    names = f"{surname}<<{given.replace(' ', '<')}"
    line1 = ("P<" + state + names).ljust(44, "<")[:44]
    body = number.ljust(9, "<")
    cd = lambda s: str(check_digit(s))
    personal = "ZE184226B<<<<<"
    line2 = (
        body
        + cd(body)
        + nationality
        + birth
        + cd(birth)
        + sex
        + expiry
        + cd(expiry)
        + personal
        + cd(personal)
    )
    composite = cd(line2[0:10] + line2[13:20] + line2[21:43])
    return line1 + line2 + composite


def dg1(mrz_text: str) -> bytes:
    inner = bytes([0x5F, 0x1F]) + _length(len(mrz_text)) + mrz_text.encode("ascii")
    return bytes([0x61]) + _length(len(inner)) + inner


def _length(n: int) -> bytes:
    return (
        bytes([n])
        if n < 0x80
        else bytes([0x81, n])
        if n < 0x100
        else bytes([0x82]) + n.to_bytes(2, "big")
    )


def lds_security_object(hashes_by_group: dict[int, bytes], algorithm: str = "sha256") -> bytes:
    entries = b"".join(
        der(0x30, integer(n) + der(0x04, h)) for n, h in sorted(hashes_by_group.items())
    )
    oid = OID_SHA1 if algorithm == "sha1" else OID[algorithm]
    return der(0x30, integer(0) + der(0x30, _oid_der(oid)) + der(0x30, entries))


def sod_cms(
    econtent: bytes,
    signer: Party,
    *,
    content_type: str = OID_LDS,
    extra_certs: tuple[x509.Certificate, ...] = (),
    wrap: bool = True,
) -> bytes:
    attrs = der(0x30, _oid_der(OID_CONTENT_TYPE) + der(0x31, _oid_der(content_type))) + der(
        0x30, _oid_der(OID_MESSAGE_DIGEST) + der(0x31, der(0x04, hashlib.sha256(econtent).digest()))
    )
    to_sign = der(0x31, attrs)
    signature = signer.key.sign(to_sign, ec.ECDSA(hashes.SHA256()))  # type: ignore[attr-defined]
    cert = signer.cert
    sid = der(0x30, cert.issuer.public_bytes() + integer(cert.serial_number))
    signer_info = der(
        0x30,
        integer(1) + sid + der(0x30, _oid_der(OID["sha256"])) + der(0xA0, attrs)
        + der(0x30, _oid_der(OID_ECDSA_SHA256)) + der(0x04, signature),
    )  # fmt: skip
    certs = der(0xA0, b"".join(c.public_bytes(Encoding.DER) for c in (cert, *extra_certs)))
    signed = der(
        0x30,
        integer(3) + der(0x31, der(0x30, _oid_der(OID["sha256"])))
        + der(0x30, _oid_der(content_type) + der(0xA0, der(0x04, econtent)))
        + certs + der(0x31, signer_info),
    )  # fmt: skip
    content_info = der(0x30, _oid_der(OID_SIGNED_DATA) + der(0xA0, signed))
    return der(0x77, content_info) if wrap else content_info


@dataclass
class Chip:
    csca: Party
    ds: Party
    data_groups: dict[int, bytes]
    sod: bytes
    mrz_text: str = ""
    meta: dict[str, object] = field(default_factory=dict)


def make_chip(
    *,
    groups: dict[int, bytes] | None = None,
    algorithm: str = "sha256",
    ds_not_before_days: int = -400,
    ds_not_after_days: int = 200,
    mrz_text: str | None = None,
    sign_with_other_key: bool = False,
    content_type: str = OID_LDS,
    csca: Party | None = None,
    wrap: bool = True,
) -> Chip:
    csca = csca or make_cert(
        "Utopia CSCA",
        ec_key(),
        ca=True,
        not_before=NOW - timedelta(days=3000),
        not_after=NOW + timedelta(days=3000),
    )
    ds = make_cert(
        "Utopia Document Signer",
        ec_key(),
        csca,
        not_before=NOW + timedelta(days=ds_not_before_days),
        not_after=NOW + timedelta(days=ds_not_after_days),
    )
    text = mrz_text or mrz_td3()
    data = (
        dict(groups)
        if groups is not None
        else {1: dg1(text), 2: b"\xff\xd8 pretend face image bytes \xff\xd9", 14: b"security info"}
    )
    construct = {
        "sha256": hashlib.sha256,
        "sha384": hashlib.sha384,
        "sha512": hashlib.sha512,
        "sha1": hashlib.sha1,
    }[algorithm]
    lds = lds_security_object({n: construct(b).digest() for n, b in data.items()}, algorithm)
    signer = Party(ec_key(), ds.cert) if sign_with_other_key else ds
    return Chip(csca, ds, data, sod_cms(lds, signer, content_type=content_type, wrap=wrap), text)
