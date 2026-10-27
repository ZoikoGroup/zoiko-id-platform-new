"""Credential status reliance cache (ZID-VC-001 §13; SIG-21). Pure domain.

A verifier may reuse a verified status list instead of fetching it for every presentation, but never for longer
than the profile's reliance SLO, and never past the list's own `ttl`. A change Zoiko itself makes (revoke, suspend)
drops the entry at once, so the SLO is the bound only for changes made elsewhere. A stale entry is a miss: the list
is fetched again, and a failed fetch is UNKNOWN (fail closed), never the old answer.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

RELIANCE_SLO = timedelta(seconds=30)
MAX_ENTRIES = 1024


@dataclass(frozen=True, slots=True)
class _Entry:
    list_claims: dict[str, Any]
    fetched_at: datetime
    ttl: timedelta


class StatusReliance:
    def __init__(self, slo: timedelta = RELIANCE_SLO, max_entries: int = MAX_ENTRIES) -> None:
        self._slo = slo
        self._max = max_entries
        self._entries: OrderedDict[str, _Entry] = OrderedDict()

    def get(self, uri: str, now: datetime) -> dict[str, Any] | None:
        entry = self._entries.get(uri)
        if entry is None:
            return None
        if now - entry.fetched_at >= min(self._slo, entry.ttl) or now < entry.fetched_at:
            del self._entries[uri]  # stale, or the clock went backwards: refetch
            return None
        return entry.list_claims

    def put(
        self, uri: str, list_claims: dict[str, Any], now: datetime, ttl_seconds: object
    ) -> None:
        ttl = (
            timedelta(seconds=ttl_seconds)
            if isinstance(ttl_seconds, int | float) and ttl_seconds > 0
            else self._slo
        )
        self._entries[uri] = _Entry(list_claims, now, ttl)
        self._entries.move_to_end(uri)
        while len(self._entries) > self._max:
            self._entries.popitem(last=False)

    def invalidate_containing(self, fragment: str) -> int:
        """Drop every entry whose URI contains `fragment` (a status list id). Returns how many went."""
        doomed = [u for u in self._entries if fragment in u]
        for u in doomed:
            del self._entries[u]
        return len(doomed)

    def __len__(self) -> int:
        return len(self._entries)


RELIANCE = StatusReliance()
