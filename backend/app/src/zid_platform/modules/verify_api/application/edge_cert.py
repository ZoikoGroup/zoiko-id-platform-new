"""Reading a partner's client certificate from the edge (CR-165; docs/specs/verify-api-mtls-domains-logos-decisions.md). FAIL CLOSED.

The TLS handshake that proves a partner holds a certificate's private key happens at the edge, not here. The cell can believe a certificate it is handed only when ALL of these hold, and says nothing when any does not:
1. the cell is configured for it: at least one trusted proxy address and a shared secret of at least 32 characters read from a file (never an environment value);
2. the TCP peer is one of those proxies;
3. the request carries the shared secret, in the header the proxy sets, compared in constant time;
4. the forwarded certificate parses, is one certificate, and is within its dates.
What is returned is only the SHA-256 fingerprint of the certificate: the cell pins fingerprints, it never trusts a certificate authority for this. With any of 1 to 4 missing the answer is `None` and the credential fails as any
wrong credential does. The edge must strip any client-supplied copy of these headers on every request: that is the edge's job, and the reason the proxy secret exists.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import urllib.parse
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cryptography import x509
from cryptography.hazmat.primitives.serialization import Encoding

MAX_HEADER = 16384
MIN_SECRET = 32


@dataclass(frozen=True, slots=True)
class EdgeConfig:
    proxies: frozenset[str] = frozenset()
    secret: bytes | None = None
    header: str = "x-forwarded-client-cert"
    secret_header: str = "x-zoiko-edge-secret"

    @property
    def enabled(self) -> bool:
        return bool(self.proxies) and self.secret is not None


def config_from_settings(settings: Any) -> EdgeConfig:
    """From the cell's settings. A proxy list with no secret, or a short secret, is a configuration mistake that stops the cell rather than leaving a half-configured trust path."""
    proxies = frozenset(
        str(p).strip() for p in getattr(settings, "partner_mtls_proxies", ()) if str(p).strip()
    )
    path = getattr(settings, "partner_mtls_proxy_secret_file", None)
    secret: bytes | None = None
    if path:
        text = Path(path).read_text(encoding="utf-8").strip()
        if len(text) < MIN_SECRET:
            raise RuntimeError(
                "ZID_PARTNER_MTLS_PROXY_SECRET_FILE must hold at least 32 characters (openssl rand -hex 32)"
            )
        secret = text.encode("utf-8")
    if proxies and secret is None:
        raise RuntimeError(
            "ZID_PARTNER_MTLS_PROXIES is set without ZID_PARTNER_MTLS_PROXY_SECRET_FILE: a forwarded certificate would be believed on the address alone"
        )
    return EdgeConfig(
        proxies,
        secret,
        str(getattr(settings, "partner_mtls_header", "x-forwarded-client-cert")).lower(),
        str(getattr(settings, "partner_mtls_secret_header", "x-zoiko-edge-secret")).lower(),
    )


def _parse(raw: str) -> x509.Certificate | None:
    """URL-encoded PEM, or base64 of the DER, exactly one certificate."""
    value = urllib.parse.unquote(raw.strip().strip('"'))
    try:
        if "BEGIN CERTIFICATE" in value:
            if value.count("BEGIN CERTIFICATE") != 1:
                return None
            return x509.load_pem_x509_certificate(value.encode())
        return x509.load_der_x509_certificate(base64.b64decode(value, validate=True))
    except (ValueError, TypeError):
        return None


def presented_fingerprint(
    cfg: EdgeConfig, *, peer: str | None, headers: Mapping[str, str], now: datetime | None = None
) -> bytes | None:
    """The fingerprint of the client certificate the edge vouches for, or None."""
    if not cfg.enabled or peer is None or peer not in cfg.proxies or cfg.secret is None:
        return None
    h = {k.lower(): v for k, v in headers.items()}
    given = h.get(cfg.secret_header)
    if not given or not hmac.compare_digest(given.encode("utf-8"), cfg.secret):
        return None
    raw = h.get(cfg.header)
    if not raw or len(raw) > MAX_HEADER:
        return None
    cert = _parse(raw)
    if cert is None:
        return None
    at = now or datetime.now(UTC)
    if cert.not_valid_before_utc > at or cert.not_valid_after_utc <= at:
        return None
    return hashlib.sha256(cert.public_bytes(Encoding.DER)).digest()
