"""Read an ICAO CSCA master list (ICAO Doc 9303 part 12; CR-095): verify its signature against an anchor the OPERATOR supplied, then extract the country
signing CA certificates it carries. Pure: bytes and certificates in, a verdict and certificates out. No network, no files.

A master list is a CMS SignedData whose content type is id-icao-cscaMasterList (2.23.136.1.1.2) and whose content is a SEQUENCE of a version and a SET of
CSCA certificates. It is signed by a Master List Signer certificate that the publishing country's own CSCA issued. So the only way to trust it is to
start from a certificate YOU confirmed out of band (the publishing country's CSCA, or its master list signer): this module refuses to read a list
without one and never treats a certificate inside the list as vouching for the list.

What a VERIFIED answer means: the list was signed by a certificate that chains to an anchor you supplied, at the time of checking, and its content
was not altered. It does not mean the certificates in it are right for you. Which countries to trust is a decision for the security owner and Legal,
and an operator compares the certificates against each country's own publication before relying on them.

Only CA certificates are returned (basicConstraints CA true). A list that carries none, or more than `MAX_CERTIFICATES`, or is larger than `MAX_BYTES`,
is refused as a whole.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from cryptography import x509
from cryptography.hazmat.primitives.serialization import Encoding

from zid_platform.modules.evidence.application import cms
from zid_platform.modules.evidence.application import epassport as ep

OID_MASTER_LIST = "2.23.136.1.1.2"
MAX_BYTES = 8 * 1024 * 1024
MAX_CERTIFICATES = 600


@dataclass(frozen=True, slots=True)
class MasterListResult:
    status: str  # VERIFIED | NOT_VERIFIED | INDETERMINATE
    reasons: tuple[str, ...]
    certificates: tuple[x509.Certificate, ...] = field(default=(), repr=False)
    signer: str | None = None
    anchor: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == "VERIFIED"


def fingerprint(cert: x509.Certificate) -> str:
    return hashlib.sha256(cert.public_bytes(Encoding.DER)).hexdigest()


def _is_ca(cert: x509.Certificate) -> bool:
    try:
        bc = cert.extensions.get_extension_for_class(x509.BasicConstraints).value
    except x509.ExtensionNotFound:
        return False
    return bool(bc.ca)


def read_master_list(
    der: bytes, *, anchors: Sequence[x509.Certificate], at: datetime | None = None
) -> MasterListResult:
    if not anchors:
        return MasterListResult("INDETERMINATE", ("NO_ANCHOR_SUPPLIED",))
    if not der or len(der) > MAX_BYTES:
        return MasterListResult("INDETERMINATE", ("MASTER_LIST_SIZE_INVALID",))
    when = at or datetime.now(UTC)
    try:
        content_type, econtent = ep._econtent(der)
        if content_type != OID_MASTER_LIST:
            return MasterListResult("INDETERMINATE", ("CONTENT_TYPE_NOT_A_MASTER_LIST",))
        certs, signer = cms._parse_cms(der)
    except (ep.ChipError, cms.DerError, IndexError, ValueError):
        return MasterListResult("INDETERMINATE", ("MASTER_LIST_UNREADABLE",))
    finding = cms._assess(
        0,
        certs,
        signer,
        econtent,
        True,
        [],
        anchors=anchors,
        when=when,
        tsa_roots=(),
        crls=(),
        ocsp_responses=(),
    )
    if not finding.digest_ok or not finding.signature_ok:
        return MasterListResult(
            "NOT_VERIFIED",
            tuple(dict.fromkeys(finding.problems or ("MASTER_LIST_SIGNATURE_INVALID",))),
            signer=finding.signer,
        )
    if not finding.algorithm_ok:
        return MasterListResult(
            "INDETERMINATE",
            tuple(finding.problems) or ("SIGNATURE_ALGORITHM_UNSUPPORTED",),
            signer=finding.signer,
        )
    if finding.chain is not cms.Chain.TRUSTED:
        return MasterListResult(
            "INDETERMINATE",
            ("MASTER_LIST_SIGNER_NOT_ISSUED_BY_A_SUPPLIED_ANCHOR",),
            signer=finding.signer,
        )
    try:
        _tag, body, end, _h = cms._tlv(econtent, 0)
        if _tag != 0x30 or end != len(econtent):
            raise cms.DerError("master list is not a sequence")
        parts = cms._children(body)
        sets = [p for p in parts if p[0] == 0x31]
        if len(sets) != 1:
            raise cms.DerError("master list has no certificate set")
        found: dict[str, x509.Certificate] = {}
        for tag, _value, whole in cms._children(sets[0][1]):
            if tag != 0x30:
                continue
            try:
                cert = x509.load_der_x509_certificate(whole)
            except ValueError:
                continue
            if _is_ca(cert):
                found[fingerprint(cert)] = cert
    except (cms.DerError, IndexError, ValueError):
        return MasterListResult(
            "NOT_VERIFIED", ("MASTER_LIST_CONTENT_UNREADABLE",), signer=finding.signer
        )
    if not found:
        return MasterListResult(
            "NOT_VERIFIED", ("MASTER_LIST_HAS_NO_CA_CERTIFICATES",), signer=finding.signer
        )
    if len(found) > MAX_CERTIFICATES:
        return MasterListResult(
            "NOT_VERIFIED", ("MASTER_LIST_TOO_MANY_CERTIFICATES",), signer=finding.signer
        )
    return MasterListResult(
        "VERIFIED",
        ("MASTER_LIST_SIGNED_BY_A_SUPPLIED_ANCHOR",),
        tuple(found[k] for k in sorted(found)),
        finding.signer,
        finding.anchor,
    )
