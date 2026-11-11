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
- revocation is checked against CRLs the operator supplies (this module never fetches anything).

What it does NOT do, and says so in every result:
- OCSP is not read, and without a supplied CRL revocation is NOT checked, so the signature can be at best VERIFIED_LIMITED;
- without a verified timestamp the signing time in the signature is CLAIMED by the signer, not trusted: validity is judged at "now";
- a signature algorithm outside the list above (SHA-1, RSASSA-PSS, DSA ...) is reported UNSUPPORTED or WEAK, never accepted;
- nothing here says the signer had lawful authority for the purpose, or that a signature is a qualified one (that needs a trust list: see
  `trust.py`).

Pure: bytes in, findings out. No network, no files.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from cryptography.x509.oid import ExtensionOID

OID_SIGNED_DATA = "1.2.840.113549.1.7.2"
OID_MESSAGE_DIGEST = "1.2.840.113549.1.9.4"
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
    anchor: str | None = None
    problems: tuple[str, ...] = ()
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
            "issuer": self.issuer,
            "claimed_signing_time": self.claimed_signing_time,
            "claimed_signing_time_trusted": False,
            "timestamp_token_present": self.timestamp_token_present,
            "timestamp": self.timestamp,
            "trusted_time": self.trusted_time,
            "revocation": self.revocation,
            "revocation_checked": self.revocation in {"GOOD", "PARTIAL"},
            "problems": list(self.problems),
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
    return _Signer(issuer, serial, ski, digest_oid, sig_oid, attrs_raw, md, st, signature, token)


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
            if ext.digest == s.sid_ski:
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
        return bool(cert.extensions.get_extension_for_oid(ExtensionOID.BASIC_CONSTRAINTS).value.ca)
    except x509.ExtensionNotFound:
        return False


# ------------------------------------------------------------------------------------------------------ trusted time


def _trusted_time(
    signer: _Signer, tsa_roots: Sequence[x509.Certificate], at: datetime
) -> tuple[str, datetime | None]:
    """Verify the signature's RFC 3161 token (the stamp covers the signature value) with the repo's own verifier. Returns (state, time)."""
    if signer.timestamp_token is None:
        return "NONE", None
    if not tsa_roots:
        return "NO_TSA_ROOTS", None
    try:
        from zid_evidence.digest import hash_bytes
        from zid_evidence.timestamp import TimestampInvalid, verify_timestamp_response
    except ImportError:  # the optional timestamp extra (pyasn1) is not installed
        return "UNAVAILABLE", None
    granted = b"\x30\x03\x02\x01\x00"  # PKIStatusInfo { status granted }
    body = granted + signer.timestamp_token
    wrapped = (
        b"\x30" + info_len(body) + body
    )  # TimeStampResp, so the existing response verifier is reused as it is
    for alg in ("sha-256", "sha-384", "sha-512"):  # the stamp's imprint may use any allowed hash
        try:
            ok = verify_timestamp_response(
                wrapped, hash_bytes(signer.signature, alg=alg), tsa_roots, now=at
            )
        except (TimestampInvalid, ValueError):
            continue
        return "VALID", ok.gen_time
    return "INVALID", None


# ------------------------------------------------------------------------------------------------------ revocation


def check_revocation(
    chain: Sequence[x509.Certificate],
    crls: Sequence[x509.CertificateRevocationList],
    at: datetime,
    now: datetime,
) -> str:
    """GOOD, PARTIAL, REVOKED or NOT_CHECKED from CRLs the operator supplied. The CRLs are read as they stand NOW (published, and not
    past their next update); a certificate they list as revoked at or before `at` (the trusted signing time, or now) is REVOKED, and
    one revoked later is not: the signature was made while it was good. A CRL counts only if its issuer's signature verifies. The trust
    anchor itself is not checked. Delta and indirect CRLs and OCSP are not read."""
    if not crls:
        return "NOT_CHECKED"
    state = "GOOD"
    for i, cert in enumerate(chain[:-1]):
        issuer = chain[i + 1]
        covering = []
        for crl in crls:
            if crl.issuer != issuer.subject or crl.last_update_utc > now:
                continue
            if crl.next_update_utc is not None and crl.next_update_utc < now:
                continue  # a list past its next update says nothing current
            try:
                if not crl.is_signature_valid(issuer.public_key()):
                    continue
            except (ValueError, TypeError):
                continue
            covering.append(crl)
        if not covering:
            state = "PARTIAL"
            continue
        for crl in covering:
            entry = crl.get_revoked_certificate_by_serial_number(cert.serial_number)
            if entry is not None and entry.revocation_date_utc <= at:
                return "REVOKED"
    return state


# ------------------------------------------------------------------------------------------------------ the report


def inspect_pdf(
    pdf: bytes,
    *,
    anchors: Sequence[x509.Certificate] = (),
    at: datetime | None = None,
    tsa_roots: Sequence[x509.Certificate] = (),
    crls: Sequence[x509.CertificateRevocationList] = (),
) -> list[Finding]:
    """One finding per embedded signature. Chain validity is judged at the TRUSTED signing time when the signature carries a timestamp
    that verifies against `tsa_roots`, otherwise at `at` (default now): the signing time inside the signature is claimed by the signer
    and is reported but never trusted. `crls` are the revocation lists the operator supplied."""
    when = at or datetime.now(UTC)
    findings: list[Finding] = []
    for i, (br, cms) in enumerate(signature_slots(pdf)):
        problems: list[str] = []
        covers = br[0] == 0 and br[2] + br[3] == len(pdf)
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
        cert = _signer_certificate(certs, signer)
        if cert is None:
            findings.append(
                Finding(
                    i,
                    covers,
                    problems=(*problems, "SIGNER_CERTIFICATE_MISSING"),
                    claimed_signing_time=signer.signing_time,
                )
            )
            continue
        hash_cls = _HASHES.get(signer.digest_oid)
        digest_ok = False
        if hash_cls is not None:
            h = hashes.Hash(hash_cls())
            h.update(_signed_content(pdf, br))
            digest_ok = signer.message_digest is not None and h.finalize() == signer.message_digest
        if not digest_ok:
            problems.append("DOCUMENT_ALTERED_AFTER_SIGNING")
        value_ok, problem = _verify_value(cert, signer)
        if problem:
            problems.append(problem)
        ts_state, ts_time = _trusted_time(signer, tsa_roots, when)
        judged_at = ts_time or when
        chain, certs_in_chain, anchor = build_chain(cert, certs, anchors, judged_at)
        revocation = (
            check_revocation(certs_in_chain, crls, judged_at, when)
            if chain is Chain.TRUSTED
            else "NOT_CHECKED"
        )
        if revocation == "REVOKED":
            problems.append("CERTIFICATE_REVOKED")
        findings.append(
            Finding(
                index=i,
                covers_whole_file=covers,
                digest_ok=digest_ok,
                signature_ok=value_ok,
                algorithm_ok=problem
                not in {
                    "UNSUPPORTED_DIGEST",
                    "UNSUPPORTED_SIGNATURE_ALGORITHM",
                    "NO_SIGNED_ATTRIBUTES",
                }
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
                anchor=anchor,
                problems=tuple(problems),
                chain_certificates=certs_in_chain,
            )
        )
    return findings
