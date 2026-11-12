"""PAdES / CMS signature verification for PDFs, on the `cryptography` library only (ZID-INT-002 section 09; DTV-02).

What this verifies, for every embedded signature of a PDF:
- the signed byte ranges cover the whole file (anything appended after signing is reported, never ignored);
- the CMS message digest equals the digest of those bytes (the document is as it was when signed);
- the signature value verifies against the signer certificate carried in the signature (RSA PKCS#1 v1.5 or ECDSA; SHA-256 and up);
- the certificate chain builds to a trust anchor the operator configured, with every certificate valid at the time used.

Trusted time and revocation, when the operator configures them:
- a signature carrying an RFC 3161 timestamp token that verifies against a configured TSA root (the repo's own `zid_evidence.timestamp`
  verifier) has a TRUSTED signing time, and the chain is judged at that time, so a certificate that has since expired is not held against
  an old signature, and one revoked before that time is;
- revocation is checked against CRLs and OCSP responses the operator supplies (this module never fetches anything).

What it does NOT do, and says so in every result:
- without a supplied CRL or OCSP response revocation is NOT checked, so the signature can be at best VERIFIED_LIMITED;
- without a verified timestamp the signing time in the signature is CLAIMED by the signer, not trusted: validity is judged at "now";
- a signature algorithm outside the list above (SHA-1, RSASSA-PSS, DSA ...) is reported UNSUPPORTED or WEAK, never accepted;
- nothing here says the signer had lawful authority for the purpose, or that a signature is a qualified one (that needs a trust list: see
  `trust.py`).

Pure: bytes in, findings out. No network, no files.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import cast

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from cryptography.hazmat.primitives.serialization import Encoding
from cryptography.x509 import ocsp
from cryptography.x509.oid import ExtendedKeyUsageOID, ExtensionOID

from zid_platform.modules.evidence.application import pdfobjects

OID_SIGNED_DATA = "1.2.840.113549.1.7.2"
OID_MESSAGE_DIGEST = "1.2.840.113549.1.9.4"
OID_SIGNING_CERT_V2 = "1.2.840.113549.1.9.16.2.47"  # CAdES / PAdES signingCertificateV2
OID_SHA256 = "2.16.840.1.101.3.4.2.1"
OID_SIGNING_TIME = "1.2.840.113549.1.9.5"
OID_TIMESTAMP_TOKEN = "1.2.840.113549.1.9.16.2.14"
_HASHES: dict[str, type[hashes.HashAlgorithm]] = {
    "2.16.840.1.101.3.4.2.1": hashes.SHA256,
    "2.16.840.1.101.3.4.2.2": hashes.SHA384,
    "2.16.840.1.101.3.4.2.3": hashes.SHA512,
}
_WEAK_HASHES = {"1.3.14.3.2.26": "SHA-1", "1.2.840.113549.2.5": "MD5"}
_RSA_SIGS = {
    "1.2.840.113549.1.1.1",
    "1.2.840.113549.1.1.11",
    "1.2.840.113549.1.1.12",
    "1.2.840.113549.1.1.13",
}
_ECDSA_SIGS = {
    "1.2.840.10045.4.3.2",
    "1.2.840.10045.4.3.3",
    "1.2.840.10045.4.3.4",
    "1.2.840.10045.2.1",
}
_BYTERANGE = re.compile(rb"/ByteRange\s*\[\s*(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s*\]")
_SUBFILTER = re.compile(rb"/SubFilter\s*/([A-Za-z0-9._-]+)")
DOCUMENT_TIMESTAMP = "ETSI.RFC3161"  # the /SubFilter of a document timestamp: a time-stamp of the whole file so far, not a signature


class Chain(StrEnum):
    TRUSTED = "TRUSTED"  # builds to a configured anchor, every certificate valid at the time used
    UNTRUSTED = "UNTRUSTED"  # a chain exists but does not end at a configured anchor
    NO_ANCHORS = "NO_ANCHORS"  # no trust anchor is configured at all
    EXPIRED = "EXPIRED"  # builds to an anchor but a certificate was not valid at the time used


@dataclass(frozen=True, slots=True)
class Finding:
    index: int
    covers_whole_file: bool = False
    digest_ok: bool = False
    signature_ok: bool = False
    algorithm_ok: bool = False
    chain: Chain = Chain.NO_ANCHORS
    signer: str | None = None
    signer_serial: str | None = None
    issuer: str | None = None
    claimed_signing_time: str | None = None
    timestamp_token_present: bool = False
    timestamp: str = "NONE"  # NONE | VALID | INVALID | NO_TSA_ROOTS | UNAVAILABLE
    trusted_time: str | None = None
    revocation: str = "NOT_CHECKED"  # NOT_CHECKED | GOOD | PARTIAL | REVOKED
    revocation_source: str = "NONE"  # NONE | OPERATOR | EMBEDDED (carried inside the signed file, used only with a trusted time)
    anchor: str | None = None
    problems: tuple[str, ...] = ()
    covered_ids: tuple[
        str, ...
    ] = ()  # XML: the element ids this signature signs (besides the whole document)
    chain_certificates: tuple[x509.Certificate, ...] = field(default=(), repr=False)

    @property
    def cryptographically_valid(self) -> bool:
        return self.digest_ok and self.signature_ok and self.algorithm_ok

    def public(self) -> dict[str, object]:
        """What a caller is told. Certificates and raw bytes stay inside."""
        return {
            "index": self.index,
            "covers_whole_file": self.covers_whole_file,
            "digest_ok": self.digest_ok,
            "signature_ok": self.signature_ok,
            "algorithm_ok": self.algorithm_ok,
            "chain": self.chain.value,
            "anchor": self.anchor,
            "signer": self.signer,
            "signer_serial": self.signer_serial,
            "signer_sha256": hashlib.sha256(
                self.chain_certificates[0].public_bytes(Encoding.DER)
            ).hexdigest()
            if self.chain_certificates
            else None,
            "issuer": self.issuer,
            "claimed_signing_time": self.claimed_signing_time,
            "claimed_signing_time_trusted": False,
            "timestamp_token_present": self.timestamp_token_present,
            "timestamp": self.timestamp,
            "trusted_time": self.trusted_time,
            "revocation": self.revocation,
            "revocation_checked": self.revocation in {"GOOD", "PARTIAL"},
            "revocation_source": self.revocation_source,
            "problems": list(self.problems),
            "covered_ids": list(self.covered_ids),
        }


# ------------------------------------------------------------------------------------------------------ DER reading


class DerError(ValueError):
    pass


def _tlv(data: bytes, pos: int) -> tuple[int, bytes, int, int]:
    """(tag, value, end position, header length) of the element at `pos`."""
    if pos + 2 > len(data):
        raise DerError("truncated")
    tag = data[pos]
    first = data[pos + 1]
    if first < 0x80:
        length, start = first, pos + 2
    else:
        n = first & 0x7F
        if n == 0 or n > 4 or pos + 2 + n > len(data):
            raise DerError("bad length")
        length, start = int.from_bytes(data[pos + 2 : pos + 2 + n], "big"), pos + 2 + n
    end = start + length
    if end > len(data):
        raise DerError("truncated")
    return tag, data[start:end], end, start - pos


def _children(value: bytes) -> list[tuple[int, bytes, bytes]]:
    """The elements inside a constructed value: (tag, value, whole encoding)."""
    out, pos = [], 0
    while pos < len(value):
        tag, v, end, _ = _tlv(value, pos)
        out.append((tag, v, value[pos:end]))
        pos = end
    return out


def _oid(value: bytes) -> str:
    if not value:
        raise DerError("empty oid")
    parts = [str(value[0] // 40), str(value[0] % 40)]
    n = 0
    for b in value[1:]:
        n = (n << 7) | (b & 0x7F)
        if not b & 0x80:
            parts.append(str(n))
            n = 0
    return ".".join(parts)


def _time(tag: int, value: bytes) -> str | None:
    text = value.decode("ascii", "replace")
    try:
        if tag == 0x17:  # UTCTime
            return datetime.strptime(text, "%y%m%d%H%M%SZ").replace(tzinfo=UTC).isoformat()
        if tag == 0x18:  # GeneralizedTime
            return datetime.strptime(text, "%Y%m%d%H%M%SZ").replace(tzinfo=UTC).isoformat()
    except ValueError:
        return None
    return None


# ------------------------------------------------------------------------------------------------------ PDF


def signature_slots(pdf: bytes) -> list[tuple[tuple[int, int, int, int], bytes]]:
    """Every (ByteRange, CMS bytes) a PDF carries. The CMS is the hex string between the two signed ranges."""
    out = []
    for m in _BYTERANGE.finditer(pdf):
        a, b, c, d = (int(g) for g in m.groups())
        if a != 0 or a + b > c or c + d > len(pdf):
            out.append(((a, b, c, d), b""))
            continue
        gap = pdf[a + b : c].strip()
        if gap.startswith(b"<") and gap.endswith(b">"):
            try:
                out.append(((a, b, c, d), bytes.fromhex(gap[1:-1].decode("ascii"))))
                continue
            except ValueError:
                pass
        out.append(((a, b, c, d), b""))
    return out


def slot_subfilters(pdf: bytes) -> list[str | None]:
    """The /SubFilter of the dictionary behind each ByteRange, in the same order as `signature_slots`. A document timestamp
    (`ETSI.RFC3161`) shares the structure of a signature but is not one: reading its token as a signature would call it broken."""
    out: list[str | None] = []
    for m in _BYTERANGE.finditer(pdf):
        start = pdf.rfind(b" obj", 0, m.start())
        end = pdf.find(b"endobj", m.end())
        window = pdf[max(start, 0) : end if end >= 0 else len(pdf)]
        found = _SUBFILTER.search(window)
        out.append(found.group(1).decode("ascii") if found else None)
    return out


def _der_prefix(data: bytes) -> bytes:
    """The DER element at the start of `data` without the zero padding a /Contents placeholder carries; empty if it does not hold together."""
    if len(data) < 2:
        return b""
    first = data[1]
    if first < 0x80:
        total = 2 + first
    else:
        n = first & 0x7F
        if n == 0 or n > 4 or len(data) < 2 + n:
            return b""
        total = 2 + n + int.from_bytes(data[2 : 2 + n], "big")
    return data[:total] if total <= len(data) else b""


def inspect_document_timestamps(
    pdf: bytes,
    *,
    tsa_roots: Sequence[x509.Certificate] = (),
    at: datetime | None = None,
) -> list[dict[str, object]]:
    """One entry per document timestamp, in file order. Each must cover everything before it (so each later one reaches further than the one
    before), carry a token that verifies against a configured TSA root with an imprint of exactly those bytes, and not be dated before the stamp
    it follows. The timestamp authority's certificate is judged at the token's own time, so an old stamp stays valid after its certificate expires."""
    when = at or datetime.now(UTC)
    out: list[dict[str, object]] = []
    previous_end = 0
    previous_time: datetime | None = None
    kinds = slot_subfilters(pdf)
    for (br, raw), kind in zip(signature_slots(pdf), kinds, strict=True):
        if kind != DOCUMENT_TIMESTAMP:
            continue
        problems: list[str] = []
        end = br[2] + br[3]
        if not (br[0] == 0 and previous_end < end <= len(pdf)):
            problems.append("BYTE_RANGE_INVALID")
        token = _der_prefix(raw)
        state, gen = "UNREADABLE", None
        if token and not problems:
            state, gen = trusted_time_from_token(token, _signed_content(pdf, br), tsa_roots, when)
        if gen is not None and previous_time is not None and gen < previous_time:
            problems.append("DATED_BEFORE_THE_EARLIER_STAMP")
        out.append(
            {
                "index": len(out),
                "state": state,
                "time": gen.isoformat() if gen else None,
                "covers_bytes": end,
                "problems": problems,
            }
        )
        previous_end = max(previous_end, end)
        previous_time = gen or previous_time
    return out


def archive_state(stamps: Sequence[dict[str, object]]) -> str:
    """NONE (no document timestamp), ARCHIVE_VALID (every stamp verified, in order), ARCHIVE_BROKEN (a stamp is invalid, mis-ordered or covers the
    wrong bytes) or ARCHIVE_UNVERIFIED (nothing is wrong that can be shown, but a stamp could not be checked, e.g. no TSA root is configured)."""
    if not stamps:
        return "NONE"
    if any(s["problems"] or s["state"] == "INVALID" for s in stamps):
        return "ARCHIVE_BROKEN"
    if all(s["state"] == "VALID" for s in stamps):
        return "ARCHIVE_VALID"
    return "ARCHIVE_UNVERIFIED"


def _signed_content(pdf: bytes, br: tuple[int, int, int, int]) -> bytes:
    a, b, c, d = br
    return pdf[a : a + b] + pdf[c : c + d]


# ------------------------------------------------------------------------------------------------------ CMS


@dataclass(frozen=True, slots=True)
class _Signer:
    sid_issuer: bytes | None
    sid_serial: int | None
    sid_ski: bytes | None
    digest_oid: str
    sig_oid: str
    signed_attrs_raw: bytes | None
    message_digest: bytes | None
    signing_time: str | None
    signature: bytes
    timestamp_token: bytes | None
    signing_cert_hash: tuple[str, bytes] | None = (
        None  # (hash OID, digest) the signer says it signed with
    )


def _parse_cms(cms: bytes) -> tuple[list[x509.Certificate], _Signer]:
    tag, outer, _, _ = _tlv(cms, 0)
    top = _children(outer)
    if tag != 0x30 or len(top) < 2 or _oid(top[0][1]) != OID_SIGNED_DATA:
        raise DerError("not CMS SignedData")
    signed = _children(_children(top[1][1])[0][1])  # [0] EXPLICIT -> SignedData SEQUENCE
    certs: list[x509.Certificate] = []
    signer_infos: list[bytes] = []
    for t, v, whole in signed[3:]:
        if t == 0xA0:  # certificates [0] IMPLICIT
            for ct, _cv, cwhole in _children(v):
                if ct == 0x30:
                    try:
                        certs.append(x509.load_der_x509_certificate(cwhole))
                    except ValueError:
                        continue
        elif t == 0x31:  # signerInfos SET
            signer_infos = [w for _t, _v, w in _children(v)]
    if len(signer_infos) != 1:
        raise DerError("expected exactly one signer")
    return certs, _parse_signer(signer_infos[0])


def _parse_signer(info: bytes) -> _Signer:
    _, value, _, _ = _tlv(info, 0)
    items = _children(value)
    sid_tag, sid_val, _ = items[1]
    issuer = serial = ski = None
    if sid_tag == 0x30:
        issuer_serial = _children(sid_val)
        issuer, serial = (
            issuer_serial[0][2],
            int.from_bytes(issuer_serial[1][1], "big", signed=True),
        )
    elif sid_tag == 0x80:
        ski = sid_val
    digest_oid = _oid(_children(items[2][1])[0][1])
    idx, attrs_raw, md, st = 3, None, None, None
    cert_hash: tuple[str, bytes] | None = None
    if items[idx][0] == 0xA0:
        attrs_raw = (
            b"\x31" + info_len(items[idx][1]) + items[idx][1]
        )  # re-tagged as SET OF for signature verification
        for _t, av, _w in _children(items[idx][1]):
            ac = _children(av)
            name, values = _oid(ac[0][1]), _children(ac[1][1])
            if name == OID_MESSAGE_DIGEST:
                md = values[0][1]
            elif name == OID_SIGNING_TIME:
                st = _time(values[0][0], values[0][1])
            elif name == OID_SIGNING_CERT_V2:
                cert_hash = _essc_cert_hash(values[0][1])
        idx += 1
    sig_oid = _oid(_children(items[idx][1])[0][1])
    signature = items[idx + 1][1]
    token: bytes | None = None
    for t, v, _w in items[idx + 2 :]:
        if t == 0xA1:
            for _t, av, _w2 in _children(v):
                parts = _children(av)
                if _oid(parts[0][1]) == OID_TIMESTAMP_TOKEN:
                    token = _children(parts[1][1])[0][
                        2
                    ]  # the ContentInfo inside the attribute's SET
    return _Signer(
        issuer, serial, ski, digest_oid, sig_oid, attrs_raw, md, st, signature, token, cert_hash
    )


def _essc_cert_hash(value: bytes) -> tuple[str, bytes] | None:
    """The first ESSCertIDv2 of a SigningCertificateV2: (hash algorithm OID, certificate hash). SHA-256 is the default when omitted."""
    try:
        certs = _children(_children(value)[0][1])
        parts = _children(certs[0][1])
        if parts[0][0] == 0x30:
            return _oid(_children(parts[0][1])[0][1]), parts[1][1]
        return OID_SHA256, parts[0][1]
    except (DerError, IndexError, ValueError):
        return None


def info_len(value: bytes) -> bytes:
    n = len(value)
    if n < 0x80:
        return bytes([n])
    body = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(body)]) + body


def _signer_certificate(certs: Sequence[x509.Certificate], s: _Signer) -> x509.Certificate | None:
    for c in certs:
        if (
            s.sid_serial is not None
            and c.serial_number == s.sid_serial
            and c.issuer.public_bytes() == s.sid_issuer
        ):
            return c
        if s.sid_ski is not None:
            try:
                ext = c.extensions.get_extension_for_oid(ExtensionOID.SUBJECT_KEY_IDENTIFIER).value
            except x509.ExtensionNotFound:
                continue
            if ext.digest == s.sid_ski:  # type: ignore[attr-defined]
                return c
    return None


def _verify_value(cert: x509.Certificate, s: _Signer) -> tuple[bool, str | None]:
    """Verify the signature value over the signed attributes (or, with none, over the content). Returns (ok, problem)."""
    if s.digest_oid in _WEAK_HASHES:
        return False, f"WEAK_DIGEST_{_WEAK_HASHES[s.digest_oid].replace('-', '')}"
    hash_cls = _HASHES.get(s.digest_oid)
    if hash_cls is None:
        return False, "UNSUPPORTED_DIGEST"
    if s.signed_attrs_raw is None:
        return (
            False,
            "NO_SIGNED_ATTRIBUTES",
        )  # a signature that does not bind the content digest is not accepted
    key = cert.public_key()
    try:
        if s.sig_oid in _RSA_SIGS and isinstance(key, rsa.RSAPublicKey):
            key.verify(s.signature, s.signed_attrs_raw, padding.PKCS1v15(), hash_cls())
        elif s.sig_oid in _ECDSA_SIGS and isinstance(key, ec.EllipticCurvePublicKey):
            key.verify(s.signature, s.signed_attrs_raw, ec.ECDSA(hash_cls()))
        else:
            return False, "UNSUPPORTED_SIGNATURE_ALGORITHM"
    except InvalidSignature:
        return False, "SIGNATURE_VALUE_INVALID"
    return True, None


# ------------------------------------------------------------------------------------------------------ chain


def build_chain(
    leaf: x509.Certificate,
    pool: Sequence[x509.Certificate],
    anchors: Sequence[x509.Certificate],
    at: datetime,
) -> tuple[Chain, tuple[x509.Certificate, ...], str | None]:
    """Walk issuer by issuer from the leaf. A certificate counts only if it was directly issued (signature verified) by the next one,
    every certificate is valid at `at`, and every issuer is a CA. Ends only at a configured anchor. Name constraints, policy
    constraints and revocation are NOT evaluated."""
    if not anchors:
        return Chain.NO_ANCHORS, (leaf,), None
    chain = [leaf]
    current = leaf
    expired = False
    for _ in range(8):
        if not (current.not_valid_before_utc <= at <= current.not_valid_after_utc):
            expired = True
        for a in anchors:
            if _issued_by(current, a):
                if not (a.not_valid_before_utc <= at <= a.not_valid_after_utc):
                    expired = True
                return (
                    (Chain.EXPIRED if expired else Chain.TRUSTED),
                    (*chain, a),
                    a.subject.rfc4514_string(),
                )
        nxt = next(
            (
                c
                for c in pool
                if c is not current and c not in chain and _issued_by(current, c) and _is_ca(c)
            ),
            None,
        )
        if nxt is None:
            break
        chain.append(nxt)
        current = nxt
    return Chain.UNTRUSTED, tuple(chain), None


def _issued_by(cert: x509.Certificate, issuer: x509.Certificate) -> bool:
    if cert.issuer != issuer.subject:
        return False
    try:
        cert.verify_directly_issued_by(issuer)
    except (ValueError, TypeError, InvalidSignature):
        return False
    return True


def _is_ca(cert: x509.Certificate) -> bool:
    try:
        return bool(cert.extensions.get_extension_for_oid(ExtensionOID.BASIC_CONSTRAINTS).value.ca)  # type: ignore[attr-defined]
    except x509.ExtensionNotFound:
        return False


# ------------------------------------------------------------------------------------------------------ trusted time


def _trusted_time(
    signer: _Signer, tsa_roots: Sequence[x509.Certificate], at: datetime
) -> tuple[str, datetime | None]:
    """Verify the signature's RFC 3161 token (the stamp covers the signature value) with the repo's own verifier. Returns (state, time)."""
    if signer.timestamp_token is None:
        return "NONE", None
    return trusted_time_from_token(signer.timestamp_token, signer.signature, tsa_roots, at)


def trusted_time_from_token(
    token: bytes, stamped: bytes, tsa_roots: Sequence[x509.Certificate], at: datetime
) -> tuple[str, datetime | None]:
    """(state, time) for an RFC 3161 TimeStampToken (CMS) whose message imprint must be a hash of `stamped`. Shared by PDF and XML."""
    if not tsa_roots:
        return "NO_TSA_ROOTS", None
    try:
        from zid_evidence.digest import hash_bytes
        from zid_evidence.timestamp import TimestampInvalid, verify_timestamp_response
    except ImportError:  # the optional timestamp extra (pyasn1) is not installed
        return "UNAVAILABLE", None
    granted = b"\x30\x03\x02\x01\x00"  # PKIStatusInfo { status granted }
    body = granted + token
    wrapped = (
        b"\x30" + info_len(body) + body
    )  # TimeStampResp, so the existing response verifier is reused as it is
    for alg in ("sha-256", "sha-384", "sha-512"):  # the stamp's imprint may use any allowed hash
        try:
            ok = verify_timestamp_response(wrapped, hash_bytes(stamped, alg=alg), tsa_roots, now=at)
        except (TimestampInvalid, ValueError):
            continue
        return "VALID", ok.gen_time
    return "INVALID", None


# ------------------------------------------------------------------------------------------------------ revocation


def _ocsp_state(
    cert: x509.Certificate,
    issuer: x509.Certificate,
    responses: Sequence[ocsp.OCSPResponse],
    now: datetime,
) -> tuple[bool, datetime | None]:
    """(covered, revoked_at) from OCSP responses the operator supplied. A response counts only if it is SUCCESSFUL, names this exact
    certificate and issuer, is current (this_update not in the future, not past next_update), and is signed by the issuer or by a
    delegated responder certificate the issuer issued for OCSP signing. SHA-1 signatures are refused like everywhere else."""
    for resp in responses:
        if resp.response_status is not ocsp.OCSPResponseStatus.SUCCESSFUL:
            continue
        if resp.serial_number != cert.serial_number:
            continue
        try:
            wanted = (
                ocsp.OCSPRequestBuilder().add_certificate(cert, issuer, resp.hash_algorithm).build()
            )
        except ValueError:
            continue
        if (
            resp.issuer_key_hash != wanted.issuer_key_hash
            or resp.issuer_name_hash != wanted.issuer_name_hash
        ):
            continue
        if resp.this_update_utc > now:
            continue
        if resp.next_update_utc is not None and resp.next_update_utc < now:
            continue
        if not _ocsp_signed_properly(resp, issuer, now):
            continue
        if resp.certificate_status is ocsp.OCSPCertStatus.GOOD:
            return True, None
        if resp.certificate_status is ocsp.OCSPCertStatus.REVOKED:
            return True, resp.revocation_time_utc
        # UNKNOWN: the responder does not know the certificate; that proves nothing either way
    return False, None


def _ocsp_signed_properly(resp: ocsp.OCSPResponse, issuer: x509.Certificate, now: datetime) -> bool:
    sig_hash = resp.signature_hash_algorithm
    if sig_hash is None or sig_hash.name in {"sha1", "md5"}:
        return False
    candidates: list[x509.Certificate] = [issuer]
    for c in resp.certificates:
        if c.issuer != issuer.subject:
            continue
        try:
            eku = cast(
                x509.ExtendedKeyUsage,
                c.extensions.get_extension_for_oid(ExtensionOID.EXTENDED_KEY_USAGE).value,
            )
        except x509.ExtensionNotFound:
            continue
        if ExtendedKeyUsageOID.OCSP_SIGNING not in eku:
            continue
        if not (c.not_valid_before_utc <= now <= c.not_valid_after_utc):
            continue
        try:
            c.verify_directly_issued_by(issuer)
        except (ValueError, TypeError, InvalidSignature):
            continue
        candidates.append(c)
    for responder in candidates:
        key = responder.public_key()
        try:
            if isinstance(key, rsa.RSAPublicKey):
                key.verify(resp.signature, resp.tbs_response_bytes, padding.PKCS1v15(), sig_hash)
                return True
            if isinstance(key, ec.EllipticCurvePublicKey):
                key.verify(resp.signature, resp.tbs_response_bytes, ec.ECDSA(sig_hash))
                return True
        except (InvalidSignature, ValueError, TypeError):
            continue
    return False


def check_revocation(
    chain: Sequence[x509.Certificate],
    crls: Sequence[x509.CertificateRevocationList],
    at: datetime,
    now: datetime,
    ocsp_responses: Sequence[ocsp.OCSPResponse] = (),
) -> str:
    """GOOD, PARTIAL, REVOKED or NOT_CHECKED from CRLs and OCSP responses the operator supplied. They are read as they stand NOW
    (published, and not past their next update); a certificate they list as revoked at or before `at` (the trusted signing time, or
    now) is REVOKED, and one revoked later is not: the signature was made while it was good. A CRL counts only if its issuer's
    signature verifies; an OCSP response only if the issuer or a delegated OCSP-signing certificate it issued signed it (see
    `_ocsp_state`). A certificate covered by neither makes the whole answer PARTIAL. The trust anchor itself is not checked. Delta and
    indirect CRLs are not read."""
    if not crls and not ocsp_responses:
        return "NOT_CHECKED"
    state = "GOOD"
    for i, cert in enumerate(chain[:-1]):
        issuer = chain[i + 1]
        covered = False
        for crl in crls:
            if crl.issuer != issuer.subject or crl.last_update_utc > now:
                continue
            if crl.next_update_utc is not None and crl.next_update_utc < now:
                continue  # a list past its next update says nothing current
            try:
                if not crl.is_signature_valid(issuer.public_key()):  # type: ignore[arg-type]
                    continue
            except (ValueError, TypeError):
                continue
            covered = True
            entry = crl.get_revoked_certificate_by_serial_number(cert.serial_number)
            if entry is not None and entry.revocation_date_utc <= at:
                return "REVOKED"
        has_answer, revoked_at = _ocsp_state(cert, issuer, ocsp_responses, now)
        if has_answer:
            covered = True
            if revoked_at is not None and revoked_at <= at:
                return "REVOKED"
        if not covered:
            state = "PARTIAL"
    return state


# ------------------------------------------------------------------------------------------------------ the report


def embedded_revocation(
    chain: Sequence[x509.Certificate],
    crls: Sequence[x509.CertificateRevocationList],
    responses: Sequence[ocsp.OCSPResponse],
    signed_at: datetime,
) -> str:
    """Revocation judged from evidence carried inside the signed file, meaningful only when the signing time is TRUSTED: the evidence must
    have been produced AFTER the signing time (evidence from before it says nothing about the signature), and each item is read as it stood
    when it was produced, because a CRL embedded years ago is long past its next update today and still proves what it proved then."""
    later_crls = [x for x in crls if x.last_update_utc >= signed_at]
    later_ocsp = [r for r in responses if r.this_update_utc >= signed_at]
    stamps = [x.last_update_utc for x in later_crls] + [r.this_update_utc for r in later_ocsp]
    if not stamps:
        return "NOT_CHECKED"
    return check_revocation(chain, later_crls, signed_at, max(stamps), later_ocsp)


def merge_embedded_revocation(
    revocation: str,
    *,
    chain_trusted: bool,
    ts_state: str,
    in_chain: Sequence[x509.Certificate],
    judged_at: datetime,
    embedded_crls: Sequence[x509.CertificateRevocationList],
    embedded_ocsp: Sequence[ocsp.OCSPResponse],
) -> tuple[str, str]:
    """(revocation, source). Operator evidence comes first and a revocation is never overridden; embedded evidence is used only for a chain
    that reaches an anchor, with a signing time fixed by a verified timestamp, and only to settle what the operator left open."""
    source = "OPERATOR" if revocation != "NOT_CHECKED" else "NONE"
    if (
        chain_trusted
        and ts_state == "VALID"
        and revocation not in {"GOOD", "REVOKED"}
        and (embedded_crls or embedded_ocsp)
    ):
        embedded = embedded_revocation(in_chain, embedded_crls, embedded_ocsp, judged_at)
        if embedded == "REVOKED" or (embedded == "GOOD" and revocation != "REVOKED"):
            return embedded, "EMBEDDED"
        if revocation == "NOT_CHECKED" and embedded != "NOT_CHECKED":
            return embedded, "EMBEDDED"
    return revocation, source


def _assess(
    index: int,
    certs: Sequence[x509.Certificate],
    signer: _Signer,
    content: bytes,
    covers: bool,
    problems: list[str],
    *,
    anchors: Sequence[x509.Certificate],
    when: datetime,
    tsa_roots: Sequence[x509.Certificate],
    crls: Sequence[x509.CertificateRevocationList],
    ocsp_responses: Sequence[ocsp.OCSPResponse],
    embedded_certs: Sequence[x509.Certificate] = (),
    embedded_crls: Sequence[x509.CertificateRevocationList] = (),
    embedded_ocsp: Sequence[ocsp.OCSPResponse] = (),
) -> Finding:
    """Everything known about ONE parsed CMS signature over `content`: digest, value, signing certificate, time, chain and revocation.
    Shared by PDF (content = the signed byte ranges) and standalone CAdES (content = the file the signature is detached from)."""
    i = index
    cert = _signer_certificate(certs, signer)
    if cert is None:
        return Finding(
            i,
            covers,
            problems=(*problems, "SIGNER_CERTIFICATE_MISSING"),
            claimed_signing_time=signer.signing_time,
        )
    hash_cls = _HASHES.get(signer.digest_oid)
    digest_ok = False
    if hash_cls is not None:
        h = hashes.Hash(hash_cls())
        h.update(content)
        digest_ok = signer.message_digest is not None and h.finalize() == signer.message_digest
    if not digest_ok:
        problems.append("DOCUMENT_ALTERED_AFTER_SIGNING")
    value_ok, problem = _verify_value(cert, signer)
    if problem:
        problems.append(problem)
    if (
        signer.signing_cert_hash is not None
    ):  # the signature names the certificate it was made with: it must be the one carried
        oid, expected = signer.signing_cert_hash
        named = _HASHES.get(oid)
        actual = None
        if named is not None:
            h2 = hashes.Hash(named())
            h2.update(cert.public_bytes(Encoding.DER))
            actual = h2.finalize()
        if actual != expected:
            problems.append("SIGNING_CERTIFICATE_DIGEST_MISMATCH")
            value_ok = False
    ts_state, ts_time = _trusted_time(signer, tsa_roots, when)
    judged_at = ts_time or when
    chain, certs_in_chain, anchor = build_chain(cert, [*certs, *embedded_certs], anchors, judged_at)
    revocation = (
        check_revocation(certs_in_chain, crls, judged_at, when, ocsp_responses)
        if chain is Chain.TRUSTED
        else "NOT_CHECKED"
    )
    revocation, revocation_source = merge_embedded_revocation(
        revocation,
        chain_trusted=chain is Chain.TRUSTED,
        ts_state=ts_state,
        in_chain=certs_in_chain,
        judged_at=judged_at,
        embedded_crls=embedded_crls,
        embedded_ocsp=embedded_ocsp,
    )
    if revocation == "REVOKED":
        problems.append("CERTIFICATE_REVOKED")
    return Finding(
        index=i,
        covers_whole_file=covers,
        digest_ok=digest_ok,
        signature_ok=value_ok,
        algorithm_ok=problem
        not in {"UNSUPPORTED_DIGEST", "UNSUPPORTED_SIGNATURE_ALGORITHM", "NO_SIGNED_ATTRIBUTES"}
        and not (problem or "").startswith("WEAK_DIGEST"),
        chain=chain,
        signer=cert.subject.rfc4514_string(),
        signer_serial=format(cert.serial_number, "x"),
        issuer=cert.issuer.rfc4514_string(),
        claimed_signing_time=signer.signing_time,
        timestamp_token_present=signer.timestamp_token is not None,
        timestamp=ts_state,
        trusted_time=ts_time.isoformat() if ts_time else None,
        revocation=revocation,
        revocation_source=revocation_source,
        anchor=anchor,
        problems=tuple(problems),
        chain_certificates=certs_in_chain,
    )


def inspect_cades(
    signature: bytes,
    content: bytes,
    *,
    anchors: Sequence[x509.Certificate] = (),
    at: datetime | None = None,
    tsa_roots: Sequence[x509.Certificate] = (),
    crls: Sequence[x509.CertificateRevocationList] = (),
    ocsp_responses: Sequence[ocsp.OCSPResponse] = (),
) -> list[Finding]:
    """A standalone CAdES signature (a detached `.p7s`, DER) over `content`: the digest is of the bytes the CALLER supplies, so a changed
    file fails exactly like a changed PDF. The signature must carry signed attributes (a message digest); one that does not is refused.
    An attached signature is checked the same way against the content the caller holds. Exactly one signer is supported."""
    when = at or datetime.now(UTC)
    try:
        certs, signer = _parse_cms(signature)
    except (DerError, IndexError, ValueError):
        return [Finding(0, True, problems=("SIGNATURE_UNREADABLE",))]
    return [
        _assess(
            0,
            certs,
            signer,
            content,
            True,
            [],
            anchors=anchors,
            when=when,
            tsa_roots=tsa_roots,
            crls=crls,
            ocsp_responses=ocsp_responses,
        )
    ]


def inspect_pdf(
    pdf: bytes,
    *,
    anchors: Sequence[x509.Certificate] = (),
    at: datetime | None = None,
    tsa_roots: Sequence[x509.Certificate] = (),
    crls: Sequence[x509.CertificateRevocationList] = (),
    ocsp_responses: Sequence[ocsp.OCSPResponse] = (),
) -> list[Finding]:
    """One finding per embedded signature. Chain validity is judged at the TRUSTED signing time when the signature carries a timestamp
    that verifies against `tsa_roots`, otherwise at `at` (default now): the signing time inside the signature is claimed by the signer
    and is reported but never trusted. `crls` and `ocsp_responses` are the revocation evidence the operator supplied."""
    when = at or datetime.now(UTC)
    findings: list[Finding] = []
    signatures = [
        slot
        for slot, kind in zip(signature_slots(pdf), slot_subfilters(pdf), strict=True)
        if kind
        != DOCUMENT_TIMESTAMP  # a document timestamp is checked on its own (inspect_document_timestamps), never as a signature
    ]
    for i, (br, cms) in enumerate(signatures):
        problems: list[str] = []
        covers = br[0] == 0 and br[2] + br[3] == len(pdf)
        embedded: pdfobjects.DssEvidence | None = None
        if not covers and br[0] == 0 and br[2] + br[3] < len(pdf):
            # PAdES long-term: validation data and document timestamps appended after signing are fine; anything else appended is a change
            update = pdfobjects.validation_update(pdf[: br[2] + br[3]], pdf[br[2] + br[3] :])
            embedded = update.evidence if update is not None else None
            covers = update is not None
        if not covers:
            problems.append("CONTENT_ADDED_AFTER_SIGNING")
        if not cms:
            findings.append(Finding(i, covers, problems=(*problems, "SIGNATURE_UNREADABLE")))
            continue
        try:
            certs, signer = _parse_cms(cms)
        except (DerError, IndexError, ValueError):
            findings.append(Finding(i, covers, problems=(*problems, "SIGNATURE_UNREADABLE")))
            continue
        findings.append(
            _assess(
                i,
                certs,
                signer,
                _signed_content(pdf, br),
                covers,
                problems,
                anchors=anchors,
                when=when,
                tsa_roots=tsa_roots,
                crls=crls,
                ocsp_responses=ocsp_responses,
                embedded_certs=embedded.certificates if embedded else (),
                embedded_crls=embedded.crls if embedded else (),
                embedded_ocsp=embedded.ocsp_responses if embedded else (),
            )
        )
    return findings
