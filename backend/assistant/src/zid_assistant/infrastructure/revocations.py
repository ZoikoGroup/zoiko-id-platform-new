"""Runtime revocation list: withdraw a source, link or capability immediately, on every replica, without a deploy (ADR-11, B07).

The signed snapshot says what is PUBLISHED. This list says what has been pulled since: it is checked at serving time, so a stale
index, a warm cache or an in-flight answer can never outlive a withdrawal. Entries are only ever *added* by an operator and are
folded into the snapshot's revocation watermark per request. A store outage keeps the last known list and never un-revokes.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, replace

import valkey.asyncio as valkey_asyncio

from zid_assistant.application.snapshot import Knowledge
from zid_assistant.settings import AssistantSettings

KINDS = ("source", "link", "capability")
_KEY = "zpa:revoked"


@dataclass(frozen=True, slots=True)
class Revocations:
    sources: frozenset[str] = frozenset()
    links: frozenset[str] = frozenset()
    capabilities: frozenset[str] = frozenset()

    def __bool__(self) -> bool:
        return bool(self.sources or self.links or self.capabilities)


def apply(k: Knowledge, rev: Revocations) -> Knowledge:
    """The knowledge with the live revocations folded into its watermark. Cheap: the registers are shared, not copied."""
    if not rev:
        return k
    snap = replace(
        k.snapshot,
        withdrawn_sources=k.snapshot.withdrawn_sources | rev.sources,
        withdrawn_links=k.snapshot.withdrawn_links | rev.links,
        withdrawn_capabilities=k.snapshot.withdrawn_capabilities | rev.capabilities,
    )
    return replace(k, snapshot=snap)


class RevocationStore:
    def __init__(
        self,
        settings: AssistantSettings,
        clock: Callable[[], float] = time.monotonic,
        ttl: float = 3.0,
    ) -> None:
        self._clock, self._ttl = clock, ttl
        self._r = (
            valkey_asyncio.from_url(settings.valkey_url, decode_responses=True)  # type: ignore[no-untyped-call]
            if settings.valkey_url
            else None
        )
        self._mem: set[str] = set()
        self._cache: tuple[float, Revocations] | None = None

    async def get(self) -> Revocations:
        now = self._clock()
        if self._cache and now - self._cache[0] < self._ttl:
            return self._cache[1]
        members = set(self._mem)
        if self._r is not None:
            try:
                members |= set(await self._r.smembers(_KEY))
            except Exception:  # noqa: BLE001 - an outage keeps what we last knew: it must never un-revoke
                if self._cache:
                    members |= {f"source:{s}" for s in self._cache[1].sources}
                    members |= {f"link:{s}" for s in self._cache[1].links}
                    members |= {f"capability:{s}" for s in self._cache[1].capabilities}
        rev = Revocations(
            frozenset(m.split(":", 1)[1] for m in members if m.startswith("source:")),
            frozenset(m.split(":", 1)[1] for m in members if m.startswith("link:")),
            frozenset(m.split(":", 1)[1] for m in members if m.startswith("capability:")),
        )
        self._cache = (now, rev)
        return rev

    async def revoke(self, kind: str, ident: str) -> None:
        if kind not in KINDS or not ident or len(ident) > 80 or ":" in ident:
            raise ValueError(f"cannot revoke {kind!r}:{ident!r}")
        member = f"{kind}:{ident}"
        if self._r is not None:
            await self._r.sadd(_KEY, member)
        self._mem.add(member)
        self._cache = None

    async def restore(self, kind: str, ident: str) -> None:
        member = f"{kind}:{ident}"
        if self._r is not None:
            await self._r.srem(_KEY, member)
        self._mem.discard(member)
        self._cache = None
