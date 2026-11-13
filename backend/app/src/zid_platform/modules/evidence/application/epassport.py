"""Passive authentication of an ePassport or eID chip (ICAO Doc 9303 parts 10 to 12), on `cryptography` only. Nothing here reads a chip: the phone app does that and
sends the signed object (EF.SOD) and the data groups it read; this module decides what they prove.

What passive authentication establishes, and nothing more:
- the EF.SOD is a CMS signature by a Document Signer certificate over a list of data-group hashes (the LDS Security Object);
- that certificate chains to a country signing CA (CSCA) THIS DEPLOYMENT CONFIGURED: with none configured nothing is trusted and the answer is
  INDETERMINATE, never a pass (`config/trust/csca/` ships empty, like the other trust folders);
- each data group the caller supplied hashes to the value the signed object lists, and a group the object does not list is refused.

What it does NOT establish, and the answer always says so: that the person holding the phone is the document's holder (no face match is made here and the
photo in DG2 is never read or kept), and that the chip is not a clone (that needs Active or Chip Authentication, which happens between chip and reader). So
the best answer is VERIFIED_LIMITED. Document Signer certificates routinely expire long before the passports they signed, so an expired signer is judged inside
its own validity window and reported as such, never silently passed.

Pure: bytes in, a verdict out. No network, no files, no storage of any data group.
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from cryptography import x509

from zid_platform.modules.evidence.application import cms
from zid_platform.modules.evidence.domain import mrz

OID_LDS_SECURITY_OBJECT = "2.23.136.1.1.1"
OID_SIGNED_DATA = "1.2.840.113549.1.7.2"
_DIGESTS = {
    "2.16.840.1.101.3.4.2.4": ("SHA-224", hashlib.sha224),
    "2.16.840.1.101.3.4.2.1": ("SHA-256", hashlib.sha256),
    "2.16.840.1.101.3.4.2.2": ("SHA-384", hashlib.sha384),
    "2.16.840.1.101.3.4.2.3": ("SHA-512", hashlib.sha512),
}
_SHA1 = "1.3.14.3.2.26"
MAX_SOD = 64 * 1024
MAX_DATA_GROUP = 256 * 1024
VALID_GROUPS = range(1, 17)

LIMITATIONS = (
    "HOLDER_NOT_BOUND: nothing here shows that the person presenting the document is the person it was issued to.",
    "CLONE_NOT_DETECTED: Active or Chip Authentication happens between the chip and the reader and is not shown to this server.",
    "The photo (DG2) is never read or stored by this check.",
)


class ChipError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


@dataclass(frozen=True, slots=True)
class SecurityObject:
    hash_name: str
    digest: object  # a hashlib constructor
    hashes: Mapping[int, bytes]


@dataclass(frozen=True, slots=True)
class ChipVerdict:
    status: str  # VERIFIED_LIMITED | NOT_VERIFIED | INDETERMINATE
    reasons: tuple[str, ...]
    detail: Mapping[str, object] = field(default_factory=dict)

    @property
    def document_authentic(self) -> bool:
        return self.status == "VERIFIED_LIMITED"

    def public(self) -> dict[str, object]:
        return {
            "status": self.status,
            "reasons": list(self.reasons),
            "document_authentic": self.document_authentic,
            "holder_bound": False,
            "limitations": list(LIMITATIONS),
            **self.detail,
        }


# --------------------------------------------------------------------------------------------------------------------------- parsing


def _strip_wrapper(sod: bytes) -> bytes:
    """EF.SOD is the CMS ContentInfo inside an application tag 0x77; the bare ContentInfo is accepted too."""
    if not sod or len(sod) > MAX_SOD:
        raise ChipError("SOD_SIZE_INVALID")
    if sod[0] == 0x77:
        try:
            tag, value, end, _h = cms._tlv(sod, 0)
        except cms.DerError as e:
            raise ChipError("SOD_UNREADABLE") from e
        if tag != 0x77 or end != len(sod):
            raise ChipError("SOD_UNREADABLE")
        return value
    return sod


def _econtent(cms_der: bytes) -> tuple[str, bytes]:
    """(content type OID, the encapsulated content) of a CMS SignedData."""
    try:
        _t, outer, _s, _e = cms._tlv(cms_der, 0)
        top = cms._children(outer)
        if cms._oid(top[0][1]) != OID_SIGNED_DATA:
            raise ChipError("SOD_NOT_SIGNED_DATA")
        signed = cms._children(cms._children(top[1][1])[0][1])
        encap = cms._children(signed[2][1])
        content_type = cms._oid(encap[0][1])
        wrapped = cms._children(encap[1][1])[0]  # [0] EXPLICIT OCTET STRING
        if wrapped[0] != 0x04:
            raise ChipError("SOD_CONTENT_NOT_OCTETS")
        return content_type, wrapped[1]
    except (cms.DerError, IndexError, ValueError) as e:
        raise ChipError("SOD_UNREADABLE") from e


def parse_security_object(content: bytes) -> SecurityObject:
    """The LDS Security Object: version, the hash algorithm, and the hash of each data group it covers."""
    try:
        _t, body, end, _h = cms._tlv(content, 0)
        if end != len(content):
            raise ChipError("LDS_TRAILING_DATA")
        items = cms._children(body)
        algorithm = cms._oid(cms._children(items[1][1])[0][1])
        entries = cms._children(items[2][1])
        hashes: dict[int, bytes] = {}
        for _et, ev, _ew in entries:
            number_raw, digest_raw = cms._children(ev)[0], cms._children(ev)[1]
            number = int.from_bytes(number_raw[1], "big")
            if number not in VALID_GROUPS or number in hashes:
                raise ChipError("LDS_DATA_GROUP_INVALID", str(number))
            hashes[number] = digest_raw[1]
    except (cms.DerError, IndexError, ValueError) as e:
        raise ChipError("LDS_UNREADABLE") from e
    if algorithm == _SHA1:
        raise ChipError("HASH_ALGORITHM_WEAK", "SHA-1")
    if algorithm not in _DIGESTS:
        raise ChipError("HASH_ALGORITHM_UNSUPPORTED", algorithm)
    name, constructor = _DIGESTS[algorithm]
    if not hashes:
        raise ChipError("LDS_NO_DATA_GROUPS")
    return SecurityObject(name, constructor, hashes)


def _ber(data: bytes, pos: int) -> tuple[int, bytes, int]:
    """(tag, value, end) of a BER element whose tag may take more than one byte (DG1 holds the two-byte tag 5F1F)."""
    if pos >= len(data):
        raise ChipError("DG1_UNREADABLE")
    tag = data[pos]
    pos += 1
    if tag & 0x1F == 0x1F:
        while True:
            if pos >= len(data):
                raise ChipError("DG1_UNREADABLE")
            more = data[pos] & 0x80
            tag = (tag << 8) | data[pos]
            pos += 1
            if not more:
                break
    if pos >= len(data):
        raise ChipError("DG1_UNREADABLE")
    first = data[pos]
    pos += 1
    if first < 0x80:
        length = first
    else:
        n = first & 0x7F
        if n == 0 or n > 3 or pos + n > len(data):
            raise ChipError("DG1_UNREADABLE")
        length, pos = int.from_bytes(data[pos : pos + n], "big"), pos + n
    if pos + length > len(data):
        raise ChipError("DG1_UNREADABLE")
    return tag, data[pos : pos + length], pos + length


def mrz_lines_from_dg1(dg1: bytes) -> list[str]:
    """The MRZ lines inside DG1 (tag 0x61 holding tag 0x5F1F), split by the document form's line length."""
    tag, body, end = _ber(dg1, 0)
    if tag != 0x61 or end != len(dg1):
        raise ChipError("DG1_UNREADABLE")
    inner_tag, text, inner_end = _ber(body, 0)
    if inner_tag != 0x5F1F or inner_end != len(body):
        raise ChipError("DG1_UNREADABLE")
    try:
        raw = text.decode("ascii")
    except UnicodeDecodeError as e:
        raise ChipError("DG1_UNREADABLE") from e
    width = {90: 30, 72: 36, 88: 44}.get(len(raw))
    if width is None:
        raise ChipError("DG1_MRZ_LENGTH_INVALID", str(len(raw)))
    return [raw[i : i + width] for i in range(0, len(raw), width)]


# ------------------------------------------------------------------------------------------------------------------------ verification


def verify(
    sod: bytes,
    data_groups: Mapping[int, bytes],
    *,
    csca: Sequence[x509.Certificate] = (),
    crls: Sequence[x509.CertificateRevocationList] = (),
    at: datetime | None = None,
    today: datetime | None = None,
) -> ChipVerdict:
    """Passive authentication. See the module note for what the answer means and does not mean."""
    when = at or datetime.now(UTC)
    reasons: list[str] = []
    detail: dict[str, object] = {}
    try:
        inner = _strip_wrapper(sod)
        content_type, econtent = _econtent(inner)
        if content_type != OID_LDS_SECURITY_OBJECT:
            return ChipVerdict("INDETERMINATE", ("SOD_CONTENT_TYPE_NOT_LDS_SECURITY_OBJECT",))
        lds = parse_security_object(econtent)
        certs, signer = cms._parse_cms(inner)
    except ChipError as e:
        return ChipVerdict(
            "NOT_VERIFIED" if e.code == "HASH_ALGORITHM_WEAK" else "INDETERMINATE", (e.code,)
        )
    except (cms.DerError, IndexError, ValueError):
        return ChipVerdict("INDETERMINATE", ("SOD_UNREADABLE",))
    detail["hash_algorithm"] = lds.hash_name
    if not data_groups:
        return ChipVerdict("INDETERMINATE", ("NO_DATA_GROUPS_SUPPLIED",), detail)

    # ---- the signature over the security object, and the chain of the signer to a configured CSCA
    problems: list[str] = []
    finding = cms._assess(
        0, certs, signer, econtent, True, problems, anchors=csca, when=when, tsa_roots=(),
        crls=crls, ocsp_responses=(),
    )  # fmt: skip
    judged_in_window = False
    if finding.chain is cms.Chain.EXPIRED:
        ds = cms._signer_certificate(certs, signer)
        if (
            ds is not None and ds.not_valid_after_utc < when
        ):  # expired, not "not yet valid": that one is never rescued
            # a signer certificate lives for months and the passport for years: judge it inside its own window, and say so
            alt = ds.not_valid_before_utc + timedelta(days=1)
            again = cms._assess(
                0, certs, signer, econtent, True, [], anchors=csca, when=alt, tsa_roots=(),
                crls=crls, ocsp_responses=(),
            )  # fmt: skip
            if again.chain is cms.Chain.TRUSTED:
                finding, judged_in_window = again, True
    detail["signer"] = finding.signer
    detail["country_signing_ca"] = finding.anchor
    detail["signer_chain"] = finding.chain.value
    if not finding.digest_ok or not finding.signature_ok:
        return ChipVerdict(
            "NOT_VERIFIED",
            tuple(dict.fromkeys(finding.problems or ("SOD_SIGNATURE_INVALID",))),
            detail,
        )
    if not finding.algorithm_ok:
        return ChipVerdict(
            "INDETERMINATE", tuple(finding.problems) or ("SIGNATURE_ALGORITHM_UNSUPPORTED",), detail
        )
    if finding.revocation == "REVOKED":
        return ChipVerdict("NOT_VERIFIED", ("DOCUMENT_SIGNER_CERTIFICATE_REVOKED",), detail)
    if finding.chain is cms.Chain.NO_ANCHORS:
        return ChipVerdict("INDETERMINATE", ("NO_CSCA_CONFIGURED",), detail)
    if finding.chain is not cms.Chain.TRUSTED:
        return ChipVerdict("INDETERMINATE", ("SIGNER_NOT_ISSUED_BY_A_CONFIGURED_CSCA",), detail)

    # ---- every supplied data group must be covered by, and match, the signed hashes
    verified: list[int] = []
    for number, content in sorted(data_groups.items()):
        if number not in VALID_GROUPS or len(content) > MAX_DATA_GROUP or not content:
            return ChipVerdict(
                "NOT_VERIFIED", ("DATA_GROUP_INVALID",), {**detail, "data_group": number}
            )
        expected = lds.hashes.get(number)
        if expected is None:
            return ChipVerdict(
                "NOT_VERIFIED", ("DATA_GROUP_NOT_COVERED_BY_SOD",), {**detail, "data_group": number}
            )
        actual = lds.digest(content).digest()  # type: ignore[operator]
        if not hmac.compare_digest(actual, expected):
            return ChipVerdict(
                "NOT_VERIFIED", ("DATA_GROUP_HASH_MISMATCH",), {**detail, "data_group": number}
            )
        verified.append(number)
    detail["data_groups_verified"] = verified
    detail["data_groups_in_sod"] = sorted(lds.hashes)

    # ---- what the chip says about the holder, read only from a data group that was verified
    if 1 in data_groups:
        try:
            result = mrz.parse(mrz_lines_from_dg1(data_groups[1]), today=(today or when).date())
        except (ChipError, mrz.MrzError) as e:
            reasons.append(f"DG1_{getattr(e, 'code', 'UNREADABLE')}")
        else:
            detail["document"] = {
                "form": result.form,
                "document_code": result.document_code,
                "issuing_state": result.issuing_state,
                "nationality": result.nationality,
                "surname": result.surname,
                "given_names": result.given_names,
                "sex": result.sex,
                "birth_date": result.birth_date_iso,
                "expiry_date": result.expiry_date_iso,
                "document_number": result.document_number.value,
                "mrz_check_digits_hold": result.all_checks_hold,
            }
            if not result.all_checks_hold:
                reasons.append("MRZ_CHECK_DIGITS_FAILED")
            expiry = result.expiry_date_iso
            if expiry and expiry < (today or when).date().isoformat():
                reasons.append("DOCUMENT_EXPIRED")
    else:
        reasons.append("DG1_NOT_SUPPLIED")

    reasons.append("SOD_SIGNED_BY_CONFIGURED_CSCA_CHAIN")
    if judged_in_window:
        reasons.append("SIGNER_CERTIFICATE_EXPIRED_JUDGED_IN_ITS_OWN_VALIDITY_WINDOW")
    if finding.revocation in {"GOOD", "PARTIAL"}:
        reasons.append("REVOCATION_CHECKED_" + finding.revocation)
    else:
        reasons.append("REVOCATION_NOT_CHECKED")
    reasons += ["HOLDER_NOT_BOUND", "CLONE_NOT_DETECTED"]
    return ChipVerdict("VERIFIED_LIMITED", tuple(dict.fromkeys(reasons)), detail)


__all__ = ["LIMITATIONS", "ChipError", "ChipVerdict", "parse_security_object", "verify"]
