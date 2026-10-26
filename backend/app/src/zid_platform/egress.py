"""Outbound-request safety shared by every server-side fetch (ZID-SEC-001 §15, SEC-19; SEC-AC-10).

A URL a tenant admin, a presenting wallet or a remote document supplies can point the platform at
the cloud metadata service, loopback or the private network. Every such fetch therefore passes
through here: the URL must be `https`, name a DNS host (never an IP literal), carry no userinfo and
use a standard port, and every address the host resolves to must be globally routable. Redirects are
never followed (callers pass `follow_redirects=False`).

`check_url` is pure and runs when a URL is accepted into configuration; `assert_public_host` does
the DNS part right before the connection (re-checked on every fetch, so a rebinding record cannot
slip a private address in later). Tests that inject an `httpx` transport never touch the network, so
they skip only the DNS step (`transport is None` gates it), never the URL check.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Iterable
from urllib.parse import urlsplit

ALLOWED_PORTS = frozenset({None, 443, 8443})
# Ranges `ipaddress` does not flag as private/reserved on every Python version.
_EXTRA_BLOCKED = (
    ipaddress.ip_network("100.64.0.0/10"),  # shared address space (CGNAT)
    ipaddress.ip_network("192.0.0.0/24"),
    ipaddress.ip_network("169.254.0.0/16"),  # link-local, incl. the metadata service
    ipaddress.ip_network("fd00:ec2::/32"),  # AWS IPv6 metadata
)


class EgressError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


def address_allowed(address: str) -> bool:
    """True only for a globally routable unicast address (IPv4-mapped IPv6 is unwrapped first)."""
    ip = ipaddress.ip_address(address.split("%", 1)[0])
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if (
        ip.is_loopback
        or ip.is_link_local
        or ip.is_private
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
        or not ip.is_global
    ):
        return False
    return not any(ip in net for net in _EXTRA_BLOCKED if ip.version == net.version)


def check_resolution(addresses: Iterable[str]) -> None:
    """Every resolved address must be public: one bad record poisons the whole name (rebinding)."""
    addrs = list(addresses)
    if not addrs or not all(address_allowed(a) for a in addrs):
        raise EgressError("EGRESS_DESTINATION_BLOCKED", "resolves to a non-public address")


def check_url(url: str) -> str:
    """Syntactic checks for an outbound URL; returns it normalized. No DNS here."""
    raw = url.strip()
    if any(ch in raw for ch in ("\\", "\r", "\n", "\t", " ")):
        raise EgressError("EGRESS_URL_INVALID", "control characters or backslashes")
    try:
        parts = urlsplit(raw)
        port = parts.port
    except ValueError as e:
        raise EgressError("EGRESS_URL_INVALID", "unparseable") from e
    if parts.scheme != "https":
        raise EgressError("EGRESS_URL_INVALID", "https is required")
    host = parts.hostname
    if not host or parts.username is not None or parts.password is not None or parts.fragment:
        raise EgressError("EGRESS_URL_INVALID", "a host is required; no userinfo or fragment")
    if port not in ALLOWED_PORTS:
        raise EgressError("EGRESS_URL_INVALID", "unsupported port")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise EgressError("EGRESS_URL_INVALID", "use a DNS name, not an IP literal")
    host = host.lower()
    if (
        host.rsplit(".", 1)[-1].isdigit()
        or host.startswith("0x")
        or host.endswith((".local", ".internal"))
    ):
        raise EgressError("EGRESS_URL_INVALID", "numeric or internal host name")
    return parts._replace(netloc=parts.netloc.lower()).geturl()


async def assert_public_host(host: str) -> None:
    """Resolve `host` now and refuse unless every address is public."""
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    except OSError as e:
        raise EgressError("EGRESS_DNS_FAILED", host) from e
    check_resolution(str(i[4][0]) for i in infos)


async def guard(url: str, *, resolve: bool = True) -> str:
    """`check_url`, then (when `resolve`) the DNS check. Callers pass `resolve=transport is None`."""
    clean = check_url(url)
    if resolve:
        await assert_public_host(urlsplit(clean).hostname or "")
    return clean


__all__ = [
    "ALLOWED_PORTS",
    "EgressError",
    "address_allowed",
    "assert_public_host",
    "check_resolution",
    "check_url",
    "guard",
]
