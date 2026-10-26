"""TLS contexts from the Crypto Policy Registry (ZID-CRYPTO-001 §11; registry profile `tls.v1`).

Any in-process TLS endpoint or client (mTLS between services, outbound webhook delivery, a local
listener) builds its `ssl.SSLContext` here instead of by hand:

- `new` (default): minimum TLS 1.3, the baseline for every new Zoiko protocol endpoint.
- `legacy`: minimum TLS 1.2, only for an endpoint that explicitly asks for the compatibility
  profile; it is a separate policy value in the registry and never the fallback of `new`.
- Clients always verify the certificate chain and hostname; there is no switch to disable that.
- Compression and renegotiation are off (library defaults kept explicit), and session tickets stay
  off unless the caller opts in, so resumption cannot weaken forward secrecy by accident.
Python's `ssl` never enables 0-RTT early data on a server context, which is the safe default for
replay-sensitive endpoints (the HTTP-side guard is `zid_app.early_data_refused`).
"""

from __future__ import annotations

import hashlib
import ssl
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from cryptography import x509

from zid_crypto import CryptoError
from zid_crypto.policy import CryptoPolicyRegistry, default_registry

TlsProfile = Literal["new", "legacy"]
_VERSIONS = {"TLS1.3": ssl.TLSVersion.TLSv1_3, "TLS1.2": ssl.TLSVersion.TLSv1_2}
_SETTING = {"new": "min_version_new_protocols", "legacy": "min_version_legacy_compat"}
PROFILE_ID = "tls.v1"


def minimum_version(
    profile: TlsProfile = "new", registry: CryptoPolicyRegistry | None = None
) -> ssl.TLSVersion:
    if profile not in _SETTING:
        raise CryptoError("CRYPTO_POLICY_INVALID", f"unknown TLS profile {profile!r}")
    name = (registry or default_registry()).profile_setting(PROFILE_ID, _SETTING[profile])
    version = _VERSIONS.get(str(name))
    if version is None:
        raise CryptoError("CRYPTO_POLICY_INVALID", f"{PROFILE_ID}: unsupported version {name!r}")
    if profile == "new" and version < ssl.TLSVersion.TLSv1_3:
        raise CryptoError("CRYPTO_POLICY_INVALID", "new protocol endpoints require TLS 1.3")
    return version


def server_context(
    certfile: str,
    keyfile: str,
    *,
    profile: TlsProfile = "new",
    registry: CryptoPolicyRegistry | None = None,
) -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = minimum_version(profile, registry)
    ctx.options |= ssl.OP_NO_COMPRESSION | ssl.OP_NO_TICKET
    ctx.load_cert_chain(certfile, keyfile)
    return ctx


def client_context(
    *,
    cafile: str | None = None,
    profile: TlsProfile = "new",
    registry: CryptoPolicyRegistry | None = None,
) -> ssl.SSLContext:
    ctx = ssl.create_default_context(ssl.Purpose.SERVER_AUTH, cafile=cafile)
    ctx.minimum_version = minimum_version(profile, registry)
    ctx.check_hostname = True
    ctx.verify_mode = ssl.CERT_REQUIRED
    return ctx


def needs_renewal(certfile: str, renew_before: timedelta, *, now: datetime | None = None) -> bool:
    """True once the leaf certificate is within `renew_before` of its expiry (or already past it)."""
    cert = x509.load_pem_x509_certificate(Path(certfile).read_bytes())
    return cert.not_valid_after_utc - (now or datetime.now(UTC)) <= renew_before


class ReloadingServerContext:
    """A server context that picks up a renewed certificate and key without a restart (CRYP-27).

    cert-manager (or any issuer) rewrites the mounted files at renewal. `reload_if_changed` loads the new pair
    into a scratch context first, so a mismatched or unreadable pair is refused and the serving pair stays in
    place; only a valid pair is applied to the live context, where it serves every new handshake. Connections
    already open keep the certificate they started with. The previous private key is not kept."""

    def __init__(
        self,
        certfile: str,
        keyfile: str,
        *,
        profile: TlsProfile = "new",
        registry: CryptoPolicyRegistry | None = None,
    ) -> None:
        self.certfile, self.keyfile = certfile, keyfile
        self.context = server_context(certfile, keyfile, profile=profile, registry=registry)
        self._stamp = self._fingerprint()

    def _fingerprint(self) -> str:
        digest = hashlib.sha256()
        for name in (self.certfile, self.keyfile):
            digest.update(Path(name).read_bytes())
        return digest.hexdigest()

    def reload_if_changed(self) -> bool:
        stamp = self._fingerprint()
        if stamp == self._stamp:
            return False
        scratch = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        scratch.load_cert_chain(
            self.certfile, self.keyfile
        )  # raises on a mismatched or invalid pair
        self.context.load_cert_chain(self.certfile, self.keyfile)
        self._stamp = stamp
        return True


__all__ = [
    "ReloadingServerContext",
    "TlsProfile",
    "client_context",
    "minimum_version",
    "needs_renewal",
    "server_context",
]
