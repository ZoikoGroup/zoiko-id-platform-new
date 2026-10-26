"""Process-level webhook plumbing shared by the API (verification) and the worker (delivery).

- `system_resolver`: the host's resolver (every address is then checked by `check_resolution`).
- `http_client`: never follows redirects, bounded timeouts; deployed cells route it through the
  SSRF-safe egress gateway (EVENTS §14) by network policy.
"""

from __future__ import annotations

import asyncio
import socket

import httpx


async def system_resolver(host: str) -> list[str]:
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return []
    return sorted({str(info[4][0]) for info in infos})


def http_client(transport: httpx.AsyncBaseTransport | None = None) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=transport,
        timeout=httpx.Timeout(10.0, connect=5.0),
        follow_redirects=False,
        headers={"User-Agent": "Zoiko-iD-Webhooks/1"},
    )
