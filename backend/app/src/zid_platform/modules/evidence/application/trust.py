"""Trust anchors and EU trusted lists for document signature checks (ZID-INT-002 section 09; DTV-02, DTV-03).

The operator decides what is trusted by putting files in `config/trust/` (nothing is shipped, so nothing is trusted by default):
- `anchors/*.pem|crt|cer`   CA certificates a signature chain may end at;
- `trusted-lists/*.xml`     ETSI TS 119 612 trusted lists (the EU LOTL's member lists), downloaded by the operator;
- `tl-signers/*.pem|crt`    the certificates a trusted list's OWN signature must verify against (published with the list of lists);
- `tsa-roots/*.pem|crt`     root certificates of the timestamp authorities whose RFC 3161 stamps are accepted as TRUSTED signing time;
- `crls/*.crl|pem`          certificate revocation lists the operator downloaded (nothing is ever fetched here).

A trusted list counts only if its XML signature verifies against one of the pinned signer certificates; an unverified list is ignored and
said to be. Qualified status then needs three things together: an authenticated list, a CA certificate in the signer's chain that the list
names as a granted qualified-certificate service at the time, and the QcStatements the signer certificate itself carries (qcCompliance and
the type: signature or seal). Anything less is NOT_ASSESSED, never a guess.

Zoiko iD is not a qualified trust service provider and none of this makes it one: it reports what a list says about someone else's service.
"""

from __future__ import annotations

import base64
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from cryptography import x509
from defusedxml import DefusedXmlException
from defusedxml import ElementTree as SafeET
from lxml import etree
from signxml import XMLVerifier

TSL = "{http://uri.etsi.org/02231/v2#}"
OID_QC_STATEMENTS = x509.ObjectIdentifier("1.3.6.1.5.5.7.1.3")
QC_COMPLIANCE = bytes.fromhex("04008e460101")  # DER content of OID 0.4.0.1862.1.1
QC_TYPE_ESIGN = bytes.fromhex("04008e46010601")  # 0.4.0.1862.1.6.1
QC_TYPE_ESEAL = bytes.fromhex("04008e46010602")  # 0.4.0.1862.1.6.2
CA_QC = "/Svctype/CA/QC"
GRANTED = "/Svcstatus/granted"
FOR_ESIG = "/ForeSignatures"
FOR_ESEAL = "/ForeSeals"


@dataclass(frozen=True, slots=True)
class Service:
    certificate: x509.Certificate
    service_type: str
    status: str
    since: datetime
    additional: tuple[str, ...]
    history: tuple[tuple[str, datetime], ...]  # earlier (status, since), oldest first


@dataclass(frozen=True, slots=True)
class TrustedList:
    name: str
    authenticated: bool
    services: tuple[Service, ...] = ()
    problem: str | None = None


@dataclass(frozen=True, slots=True)
class TrustConfig:
    anchors: tuple[x509.Certificate, ...] = ()
    trusted_lists: tuple[TrustedList, ...] = ()
    tsa_roots: tuple[x509.Certificate, ...] = ()
    crls: tuple[x509.CertificateRevocationList, ...] = ()

    def notes(self) -> list[str]:
        out = []
        if not self.anchors:
            out.append("NO_TRUST_ANCHORS_CONFIGURED")
        if not any(t.authenticated for t in self.trusted_lists):
            out.append("NO_AUTHENTICATED_TRUST_LIST")
        if not self.tsa_roots:
            out.append("NO_TSA_ROOTS_CONFIGURED")
        if not self.crls:
            out.append("NO_CRLS_SUPPLIED")
        return out


def _certs(folder: Path, patterns: Sequence[str]) -> list[x509.Certificate]:
    out: list[x509.Certificate] = []
    if not folder.is_dir():
        return out
    for pattern in patterns:
        for f in sorted(folder.glob(pattern)):
            data = f.read_bytes()
            try:
                out.append(
                    x509.load_pem_x509_certificate(data)
                    if b"BEGIN CERTIFICATE" in data
                    else x509.load_der_x509_certificate(data)
                )
            except ValueError:
                continue  # a file that is not a certificate is skipped, never trusted
    return out


def _crls(folder: Path) -> list[x509.CertificateRevocationList]:
    out: list[x509.CertificateRevocationList] = []
    if not folder.is_dir():
        return out
    for f in sorted([*folder.glob("*.crl"), *folder.glob("*.pem")]):
        data = f.read_bytes()
        try:
            out.append(
                x509.load_pem_x509_crl(data)
                if b"BEGIN X509 CRL" in data
                else x509.load_der_x509_crl(data)
            )
        except ValueError:
            continue
    return out


def _when(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        return datetime.fromisoformat(text).astimezone(UTC)
    except ValueError:
        return None


def parse_trusted_list(
    xml: bytes, signers: Sequence[x509.Certificate], name: str = "list"
) -> TrustedList:
    """Parse one list. It is `authenticated` only if its signature verifies against a pinned signer; otherwise it is returned with no
    services, so nothing in it can be relied on."""
    from cryptography.hazmat.primitives.serialization import Encoding

    # a trusted list never needs a DTD, entity or external reference: refuse any before lxml sees the bytes (the repo's XML gate, as for SAML)
    try:
        SafeET.fromstring(xml, forbid_dtd=True, forbid_entities=True, forbid_external=True)
        root = etree.fromstring(
            xml, parser=etree.XMLParser(resolve_entities=False, no_network=True)
        )
    except (DefusedXmlException, etree.XMLSyntaxError, ValueError, SafeET.ParseError):
        return TrustedList(name, False, problem="NOT_XML_OR_UNSAFE")
    verified = False
    for signer in signers:
        try:
            XMLVerifier().verify(xml, x509_cert=signer.public_bytes(Encoding.PEM).decode())
            verified = True
            break
        except Exception:  # noqa: BLE001, S112 - any failure to verify against this signer means "not verified by it"
            continue
    if not verified:
        return TrustedList(name, False, problem="LIST_SIGNATURE_NOT_VERIFIED")
    services: list[Service] = []
    for svc in root.iter(f"{TSL}ServiceInformation"):
        cert_b64 = svc.findtext(f".//{TSL}X509Certificate")
        kind = svc.findtext(f"{TSL}ServiceTypeIdentifier") or ""
        status = svc.findtext(f"{TSL}ServiceStatus") or ""
        since = _when(svc.findtext(f"{TSL}StatusStartingTime"))
        if not cert_b64 or since is None:
            continue
        try:
            cert = x509.load_der_x509_certificate(base64.b64decode("".join(cert_b64.split())))
        except ValueError:
            continue
        extra = tuple(u.text or "" for u in svc.iter(f"{TSL}URI"))
        hist = []
        parent = svc.getparent()
        for h in parent.iter(f"{TSL}ServiceHistoryInstance") if parent is not None else ():
            hs, ht = (
                h.findtext(f"{TSL}ServiceStatus") or "",
                _when(h.findtext(f"{TSL}StatusStartingTime")),
            )
            if ht is not None:
                hist.append((hs, ht))
        services.append(
            Service(cert, kind, status, since, extra, tuple(sorted(hist, key=lambda x: x[1])))
        )
    return TrustedList(name, True, tuple(services))


def load_trust(root: Path) -> TrustConfig:
    """Read `root` (the `config/trust` folder). A missing folder, or an empty one, is a valid configuration that trusts nothing."""
    anchors = _certs(root / "anchors", ("*.pem", "*.crt", "*.cer"))
    signers = _certs(root / "tl-signers", ("*.pem", "*.crt", "*.cer"))
    lists = []
    folder = root / "trusted-lists"
    if folder.is_dir():
        for f in sorted(folder.glob("*.xml")):
            lists.append(parse_trusted_list(f.read_bytes(), signers, f.name))
    tsa = _certs(root / "tsa-roots", ("*.pem", "*.crt", "*.cer"))
    return TrustConfig(tuple(anchors), tuple(lists), tuple(tsa), tuple(_crls(root / "crls")))


# ------------------------------------------------------------------------------------------------ qualified status


def _status_at(svc: Service, at: datetime) -> str | None:
    """The status the list gave the service at `at`: the current one if it started by then, otherwise the history entry in force."""
    if svc.since <= at:
        return svc.status
    for status, since in reversed(svc.history):
        if since <= at:
            return status
    return None


@dataclass(frozen=True, slots=True)
class Qualified:
    status: str  # QUALIFIED_ESIG | QUALIFIED_ESEAL | NOT_QUALIFIED | NOT_ASSESSED
    reasons: tuple[str, ...] = ()
    service_type: str | None = None


def _qc_statements(cert: x509.Certificate) -> bytes:
    try:
        return bytes(cert.extensions.get_extension_for_oid(OID_QC_STATEMENTS).value.public_bytes())
    except (x509.ExtensionNotFound, AttributeError):
        return b""


def qualified_status(
    chain: Sequence[x509.Certificate], trust: TrustConfig, at: datetime
) -> Qualified:
    """What the configured, authenticated lists say about the signer's CA at `at`. NOT_ASSESSED unless a list was authenticated."""
    lists = [t for t in trust.trusted_lists if t.authenticated]
    if not lists:
        return Qualified("NOT_ASSESSED", ("NO_AUTHENTICATED_TRUST_LIST",))
    if not chain:
        return Qualified("NOT_ASSESSED", ("NO_CHAIN",))
    leaf, cas = chain[0], chain[1:]
    for tl in lists:
        for svc in tl.services:
            if CA_QC not in svc.service_type or not any(svc.certificate == c for c in cas):
                continue
            if _status_at(svc, at) is None or GRANTED not in (_status_at(svc, at) or ""):
                return Qualified(
                    "NOT_QUALIFIED", ("SERVICE_NOT_GRANTED_AT_THE_TIME",), svc.service_type
                )
            qc = _qc_statements(leaf)
            if QC_COMPLIANCE not in qc:
                return Qualified(
                    "NOT_QUALIFIED", ("CERTIFICATE_LACKS_QC_COMPLIANCE",), svc.service_type
                )
            names_seals = any(FOR_ESEAL in u for u in svc.additional)
            if QC_TYPE_ESEAL in qc or (names_seals and QC_TYPE_ESIGN not in qc):
                return Qualified("QUALIFIED_ESEAL", (), svc.service_type)
            return Qualified("QUALIFIED_ESIG", (), svc.service_type)
    return Qualified("NOT_QUALIFIED", ("CA_NOT_ON_A_LIST",))


__all__ = [
    "Qualified",
    "Service",
    "TrustConfig",
    "TrustedList",
    "load_trust",
    "parse_trusted_list",
    "qualified_status",
]
