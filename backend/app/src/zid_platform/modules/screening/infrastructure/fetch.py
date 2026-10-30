"""Fetching sanctions list files (ZID-ED-04 section 07: list deltas at least daily where the feed permits).

Every address passes the platform's outbound-request guard (https, a DNS name, a standard port, only public addresses). A
redirect is followed only to a host the source names (the UN file redirects to a short-lived signed blob address), over https,
at most three times. The body is size-bounded and read whole before anything is parsed. Any failure is a result code, never an
exception that could stop the worker, and never changes the list in use: it simply ages, and a list that is too old makes every
screening UNAVAILABLE rather than quietly clear.
"""

from __future__ import annotations

from collections.abc import Mapping
from urllib.parse import urljoin, urlsplit

import httpx

from zid_platform.egress import EgressError, guard
from zid_platform.modules.screening.infrastructure.config import Source

MAX_BYTES = 80_000_000  # the UK list is tens of megabytes; anything larger is not a list
TIMEOUT_S = 60.0
MAX_REDIRECTS = 3


class FetchError(Exception):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


async def _get(client: httpx.AsyncClient, url: str, source: Source, *, resolve: bool) -> bytes:
    allowed = {urlsplit(u).hostname for u in source.urls.values()} | set(source.redirect_hosts)
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        try:
            await guard(current, resolve=resolve)
        except EgressError as e:
            raise FetchError("FETCH_BLOCKED", e.code) from e
        if urlsplit(current).hostname not in allowed:
            raise FetchError("FETCH_REDIRECT_REFUSED", "a host the source does not name")
        try:
            r = await client.get(current)
        except httpx.HTTPError as e:
            raise FetchError("FETCH_FAILED", type(e).__name__) from e
        if r.status_code in (301, 302, 303, 307, 308):
            location = r.headers.get("location")
            if not location:
                raise FetchError("FETCH_FAILED", "a redirect with no address")
            current = urljoin(current, location)
            continue
        if r.status_code != 200:
            raise FetchError("FETCH_FAILED", f"source answered {r.status_code}")
        if len(r.content) > MAX_BYTES:
            raise FetchError("FETCH_TOO_LARGE")
        if not r.content:
            raise FetchError("FETCH_FAILED", "empty body")
        return r.content
    raise FetchError("FETCH_REDIRECT_REFUSED", "too many redirects")


async def fetch_source(
    source: Source, *, transport: httpx.AsyncBaseTransport | None = None
) -> Mapping[str, bytes]:
    """Every file of one list, or a FetchError. Nothing is returned for a source with no verified address."""
    if not source.configured:
        raise FetchError("NO_ADDRESS")
    files: dict[str, bytes] = {}
    async with httpx.AsyncClient(
        transport=transport, timeout=TIMEOUT_S, follow_redirects=False, verify=True
    ) as client:
        for name, url in source.urls.items():
            files[name] = await _get(client, url, source, resolve=transport is None)
    return files
