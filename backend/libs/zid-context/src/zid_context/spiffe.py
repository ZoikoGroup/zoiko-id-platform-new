"""SPIFFE workload identity: parse, verify, and read the peer from the mesh (DEC-F7; CRYPTO-MI-001
§25, §27, §29; AI Agent spec §5).

An SVID *authenticates* a workload; it never authorizes it. The machine module maps a verified
SPIFFE ID to a registered workload binding and a logical principal; authority, mandate and policy
are evaluated after that. Never authorize from a certificate CN, a display name or a header the
caller could set.

- `SpiffeId.parse`: `spiffe://<trust-domain>/<path>`, strict (lower-case trust domain, no port,
  user info, query, fragment, dot segments or empty segments).
- `TrustPolicy`: the cell's own trust domain plus explicitly federated ones. There is no
  trust-on-first-use: an unlisted trust domain is refused (DEC-F7 §2).
- `verify_x509_svid`: leaf + intermediates against the trust bundle of the SVID's own trust
  domain; exactly one URI SAN; a leaf (not a CA); within validity; digitalSignature usage.
- `peer_spiffe_id`: the verified peer of the current connection. Sources, in order of trust:
  the ASGI TLS extension (direct mTLS to this process), else the mesh-forwarded client certificate
  (Istio XFCC) only when the immediate peer is a configured local proxy and the header was set by it.
"""

from __future__ import annotations

import re
import urllib.parse
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

__all__ = [
    "SpiffeError",
    "SpiffeId",
    "TrustPolicy",
    "peer_spiffe_id",
    "verify_x509_svid",
]

_TRUST_DOMAIN = re.compile(r"^[a-z0-9._-]{1,255}$")
_SEGMENT = re.compile(r"^[A-Za-z0-9._-]+$")


class SpiffeError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


@dataclass(frozen=True, slots=True)
class SpiffeId:
    trust_domain: str
    path: str  # "/ns/prod/sa/payments" (never empty for a workload)

    @classmethod
    def parse(cls, value: str) -> SpiffeId:
        if not isinstance(value, str) or len(value) > 2048 or not value.startswith("spiffe://"):
            raise SpiffeError("SPIFFE_ID_INVALID", "not a spiffe:// URI")
        rest = value[len("spiffe://") :]
        domain, _, path = rest.partition("/")
        if not _TRUST_DOMAIN.match(domain) or ".." in domain:
            raise SpiffeError("SPIFFE_ID_INVALID", "trust domain")
        if any(c in value for c in "?#@%") or ":" in domain:
            raise SpiffeError("SPIFFE_ID_INVALID", "no query, fragment, user info, port or escapes")
        segments = path.split("/")
        if not path or any(not s or s in (".", "..") or not _SEGMENT.match(s) for s in segments):
            raise SpiffeError("SPIFFE_ID_INVALID", "workload path")
        return cls(domain, "/" + path)

    def __str__(self) -> str:
        return f"spiffe://{self.trust_domain}{self.path}"


@dataclass(frozen=True, slots=True)
class TrustPolicy:
    """The cell's trust domain and explicitly federated ones, each with its own CA bundle (DER)."""

    own_domain: str
    bundles: Mapping[str, Sequence[bytes]] = field(default_factory=dict)
    federated: frozenset[str] = frozenset()
    trusted_proxies: frozenset[str] = frozenset()  # e.g. {"127.0.0.1"} for a local mesh proxy

    def accepts(self, sid: SpiffeId) -> bool:
        return sid.trust_domain == self.own_domain or sid.trust_domain in self.federated

    def require(self, sid: SpiffeId) -> SpiffeId:
        if not self.accepts(sid):
            raise SpiffeError("SPIFFE_TRUST_DOMAIN_UNTRUSTED", sid.trust_domain)
        return sid


def _load(der: bytes) -> Any:
    from cryptography import x509

    return x509.load_der_x509_certificate(der)


def _verify_signature(child: Any, issuer: Any) -> bool:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric import ec, ed25519, padding, rsa

    key = issuer.public_key()
    try:
        if isinstance(key, ec.EllipticCurvePublicKey):
            key.verify(child.signature, child.tbs_certificate_bytes, ec.ECDSA(child.signature_hash_algorithm))
        elif isinstance(key, rsa.RSAPublicKey):
            key.verify(child.signature, child.tbs_certificate_bytes, padding.PKCS1v15(), child.signature_hash_algorithm)
        elif isinstance(key, ed25519.Ed25519PublicKey):
            key.verify(child.signature, child.tbs_certificate_bytes)
        else:
            return False
    except InvalidSignature:
        return False
    return child.issuer == issuer.subject


def _spiffe_uri(cert: Any) -> str:
    from cryptography import x509

    try:
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    except x509.ExtensionNotFound as e:
        raise SpiffeError("SVID_INVALID", "no SAN") from e
    uris = san.get_values_for_type(x509.UniformResourceIdentifier)
    if len(uris) != 1:
        raise SpiffeError("SVID_INVALID", "an X.509-SVID has exactly one URI SAN")
    return str(uris[0])


def verify_x509_svid(
    chain: Sequence[bytes], policy: TrustPolicy, *, now: datetime | None = None
) -> SpiffeId:
    """Verify an X.509-SVID chain (leaf first, DER) and return its SPIFFE ID."""
    from cryptography import x509

    now = now or datetime.now(UTC)
    if not chain:
        raise SpiffeError("SVID_MISSING")
    certs = [_load(c) for c in chain]
    leaf = certs[0]
    sid = policy.require(SpiffeId.parse(_spiffe_uri(leaf)))
    try:
        bc = leaf.extensions.get_extension_for_class(x509.BasicConstraints).value
        if bc.ca:
            raise SpiffeError("SVID_INVALID", "the leaf must not be a CA")
    except x509.ExtensionNotFound:
        pass
    try:
        ku = leaf.extensions.get_extension_for_class(x509.KeyUsage).value
        if not ku.digital_signature:
            raise SpiffeError("SVID_INVALID", "digitalSignature key usage required")
    except x509.ExtensionNotFound:
        pass
    anchors = [_load(c) for c in policy.bundles.get(sid.trust_domain, ())]
    if not anchors:
        raise SpiffeError("SPIFFE_BUNDLE_MISSING", sid.trust_domain)
    for c in certs:
        if not (c.not_valid_before_utc <= now <= c.not_valid_after_utc):
            raise SpiffeError("SVID_EXPIRED", str(c.subject))
    current = leaf
    for intermediate in certs[1:]:
        if not _verify_signature(current, intermediate):
            raise SpiffeError("SVID_CHAIN_INVALID", "intermediate does not sign the chain")
        current = intermediate
    if not any(_verify_signature(current, a) and a.not_valid_after_utc >= now for a in anchors):
        raise SpiffeError("SVID_CHAIN_INVALID", "not issued under the trust domain's bundle")
    return sid


def _xfcc_cert(header: str) -> bytes | None:
    """The first element's URL-encoded PEM `Cert=` from an Envoy x-forwarded-client-cert header."""
    first = header.split(",")[0]
    for part in first.split(";"):
        key, _, value = part.partition("=")
        if key.strip().lower() == "cert":
            pem = urllib.parse.unquote(value.strip().strip('"'))
            from cryptography import x509
            from cryptography.hazmat.primitives.serialization import Encoding

            return x509.load_pem_x509_certificate(pem.encode()).public_bytes(Encoding.DER)
    return None


def peer_spiffe_id(
    scope: Mapping[str, Any],
    headers: Mapping[str, str],
    policy: TrustPolicy,
    *,
    now: datetime | None = None,
    xfcc_chain: Iterable[bytes] = (),
) -> SpiffeId:
    """The mesh-verified peer of this request. Raises SpiffeError when there is none.

    A forwarded certificate is honored only when the TCP peer is a trusted local proxy (the mesh
    data plane), so a client reaching the process directly cannot claim an identity by header."""
    tls = (scope.get("extensions") or {}).get("tls") or {}
    chain = tls.get("client_cert_chain") or []
    if chain:
        ders = [c if isinstance(c, bytes) else _pem_to_der(c) for c in chain]
        return verify_x509_svid(ders, policy, now=now)
    client = scope.get("client") or ("", 0)
    header = headers.get("x-forwarded-client-cert")
    if header and client[0] in policy.trusted_proxies:
        der = _xfcc_cert(header)
        if der is not None:
            return verify_x509_svid([der, *xfcc_chain], policy, now=now)
    raise SpiffeError("SVID_MISSING", "no mesh-verified peer identity")


def _pem_to_der(pem: str) -> bytes:
    from cryptography import x509
    from cryptography.hazmat.primitives.serialization import Encoding

    return x509.load_pem_x509_certificate(pem.encode()).public_bytes(Encoding.DER)
