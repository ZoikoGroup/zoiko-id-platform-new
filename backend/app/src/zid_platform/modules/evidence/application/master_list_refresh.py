"""Scheduled fetch of published CSCA master lists into the governed trust store (WF01, CR-107, CR-114).

It only ever STAGES. A fetched list is recorded with its chain of custody and then waits for a person to validate it, a second person to approve it and activate it (`application.trust_store`). Nothing
here validates, approves, activates or trusts anything, so a compromised or mistaken address can put a candidate in front of a reviewer and no more.

Default off: `backend/config/pki/master-list-sources.yaml` ships with no source. A source names the address, the tenant that holds the store, the anchor certificates the security owner confirmed out
of band (the file path and the SHA-256 they typed back) and nothing else. An address must pass the same web-address check as every other outbound fetch in the evidence module (http or https, no private,
loopback or link-local address, size cap, short timeout, few redirects). A list that has not changed since the last staging (same SHA-256 for that source) is not staged again.
"""

from __future__ import annotations

import hashlib
import logging
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

import yaml
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.evidence.application import trust_store
from zid_platform.modules.evidence.domain.trust_store import TrustStoreError
from zid_platform.modules.evidence.infrastructure import trust_package_store as store
from zid_platform.modules.evidence.infrastructure.crl import acceptable_url

log = logging.getLogger("zid.evidence.master_list_refresh")

ACTOR = "system:master-list-refresh"
MAX_BYTES = 8 * 1024 * 1024
CONFIG = Path(
    os.environ.get("ZID_PLATFORM_CONFIG") or Path(__file__).resolve().parents[5] / "config"
)
FILE = "pki/master-list-sources.yaml"
_KEYS = frozenset({"id", "url", "tenant_id", "anchor_files", "anchor_sha256", "note"})


class ConfigError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Source:
    id: str
    url: str
    tenant_id: str
    anchors: tuple[bytes, ...]
    anchor_sha256: tuple[str, ...]
    note: str | None


class Fetcher(Protocol):
    async def get(self, url: str) -> bytes | None:
        """The body, or None when it could not be had. Never raises for a network fault."""


class HttpFetcher:
    def __init__(self, *, timeout_s: float = 20.0, max_redirects: int = 3) -> None:
        self.timeout_s, self.max_redirects = timeout_s, max_redirects

    async def get(self, url: str) -> bytes | None:
        import httpx

        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_s, follow_redirects=True, max_redirects=self.max_redirects
            ) as http:
                r = await http.get(url)
        except httpx.HTTPError:
            return None
        if r.status_code != 200 or not r.content or len(r.content) > MAX_BYTES:
            return None
        return bytes(r.content)


def parse(doc: Mapping[str, Any] | None, *, root: Path) -> list[Source]:
    sources: list[Source] = []
    seen: set[str] = set()
    for body in (doc or {}).get("sources") or ():
        if not isinstance(body, dict):
            raise ConfigError("a source must be a mapping")
        unknown = set(body) - _KEYS
        if unknown:
            raise ConfigError(f"unknown key {min(unknown)}")
        sid, url, tenant = (str(body.get(k) or "") for k in ("id", "url", "tenant_id"))
        if not sid or not tenant or sid in seen:
            raise ConfigError("each source needs a unique id and a tenant_id")
        seen.add(sid)
        if not acceptable_url(url):
            raise ConfigError(f"{sid}: the address is not an acceptable public web address")
        files = [str(f) for f in body.get("anchor_files") or ()]
        fingerprints = [str(f).lower() for f in body.get("anchor_sha256") or ()]
        if not files or not fingerprints:
            raise ConfigError(f"{sid}: anchor files and their confirmed SHA-256 are required")
        sources.append(
            Source(
                sid,
                url,
                tenant,
                tuple((root / f).read_bytes() for f in files),
                tuple(fingerprints),
                body.get("note"),
            )
        )
    return sources


def load(root: Path | None = None) -> list[Source]:
    base = root or CONFIG
    path = base / FILE
    if not path.exists():
        return []
    return parse(yaml.safe_load(path.read_text(encoding="utf8")), root=base)


async def refresh_pass(
    conn: AsyncConnection,
    now: datetime,
    *,
    sources: Sequence[Source],
    fetcher: Fetcher,
    in_tenant: Any,
) -> dict[str, int]:
    """Fetch each source once and stage what changed. Returns counts: staged, unchanged, unavailable, refused."""
    counts = {"staged": 0, "unchanged": 0, "unavailable": 0, "refused": 0}
    for source in sources:
        body = await fetcher.get(source.url)
        if body is None:
            counts["unavailable"] += 1
            log.warning("master list not available", extra={"source": source.id})
            continue
        digest = hashlib.sha256(body).hexdigest()
        async with in_tenant(conn, source.tenant_id) as scoped:
            known = {r["package_sha256"] for r in await store.listing(scoped)}
            if digest in known:
                counts["unchanged"] += 1
                continue
            try:
                await trust_store.stage(
                    scoped,
                    tenant_id=source.tenant_id,
                    actor=ACTOR,
                    master_list=body,
                    anchors=source.anchors,
                    confirmed_anchor_sha256=source.anchor_sha256,
                    source_uri=source.url,
                    source_note=source.note or f"scheduled fetch ({source.id})",
                    now=now,
                )
            except TrustStoreError as e:
                counts["refused"] += 1
                log.warning("master list refused", extra={"source": source.id, "code": e.code})
                continue
        counts["staged"] += 1
    return counts
