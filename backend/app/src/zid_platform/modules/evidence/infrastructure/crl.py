"""Fetching certificate revocation lists for a timestamp authority's certificate chain (DEC-F2 §3).

Only ever called with the distribution points of certificates that have already been verified as chaining to a configured
root, so the addresses are the issuing CA's own. Even so: http(s) only, no loopback, private or link-local literals,
no more than a few redirects, a size cap and a short timeout. A list that cannot be fetched or parsed is skipped, never
fatal here; whether a missing list matters is decided by the revocation mode (`zid_evidence.timestamp.check_revocation`).
"""

from __future__ import annotations

import ipaddress
import logging
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Protocol
from urllib.parse import urlsplit

from cryptography import x509
from zid_evidence.timestamp import load_crls

log = logging.getLogger("zid.evidence.crl")


class CrlSource(Protocol):
    async def fetch(self, urls: Sequence[str]) -> list[x509.CertificateRevocationList]: ...


def acceptable_url(url: str) -> bool:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if (
        parts.scheme not in ("http", "https")
        or not host
        or host == "localhost"
        or host.endswith(".localhost")
    ):
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return True  # a name: resolved by the network, not judged here
    return not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
    )


class CrlFetcher:
    def __init__(
        self,
        *,
        timeout_s: float = 5.0,
        max_bytes: int = 4_000_000,
        max_redirects: int = 3,
        ttl: timedelta = timedelta(hours=1),
    ) -> None:
        self.timeout_s = timeout_s
        self.max_bytes = max_bytes
        self.max_redirects = max_redirects
        self.ttl = ttl
        self._cache: dict[str, tuple[datetime, list[x509.CertificateRevocationList]]] = {}

    async def fetch(self, urls: Sequence[str]) -> list[x509.CertificateRevocationList]:
        import httpx

        found: list[x509.CertificateRevocationList] = []
        now = datetime.now(UTC)
        async with httpx.AsyncClient(
            timeout=self.timeout_s, follow_redirects=True, max_redirects=self.max_redirects
        ) as http:
            for url in dict.fromkeys(urls):
                cached = self._cache.get(url)
                if (
                    cached
                    and now - cached[0] < self.ttl
                    and all(_unexpired(c, now) for c in cached[1])
                ):
                    found.extend(cached[1])
                    continue
                if not acceptable_url(url):
                    log.warning("CRL address refused", extra={"url": url})
                    continue
                try:
                    r = await http.get(url)
                    if r.status_code != 200 or len(r.content) > self.max_bytes:
                        raise ValueError(f"status {r.status_code}, {len(r.content)} bytes")
                    crls = list(load_crls(r.content))
                except (httpx.HTTPError, ValueError) as e:
                    log.warning("CRL not available", extra={"url": url, "why": type(e).__name__})
                    continue
                self._cache[url] = (now, crls)
                found.extend(crls)
        return found


def _unexpired(crl: x509.CertificateRevocationList, now: datetime) -> bool:
    return crl.next_update_utc is None or crl.next_update_utc >= now
