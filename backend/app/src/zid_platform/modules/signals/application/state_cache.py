"""Revision-aware security-state cache, bounded by risk class (ZID-SIGNALS-001 §14; FR-SIG-006).

Reading the authoritative state on every request is the safe default; this cache lets low-risk
traffic skip it for a short, risk-bounded window without ever going backwards:
- TTL by risk class (domain.rules.STATE_CACHE_TTL): R0 30 s, R1 5 s, R2/R3 never cached - a
  protected or high-risk action always reads the committed state in its own transaction.
- revision-aware: an entry carries the state revision it saw. A newer revision written in this
  process (signal ingest, governed override) invalidates older entries at once, and a read that
  returns an older revision than one already seen is never cached (no regression).
- bounded size: oldest entries are evicted first.
Other processes learn of a change only through their own reads, so for them the TTL is the
staleness bound - which is exactly why R2/R3 bypass the cache.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncConnection
from zid_telemetry import get_logger
from zid_telemetry.metrics import REGISTRY

from zid_platform.modules.signals.domain.rules import CurrentState, cache_fresh, pdp_state
from zid_platform.modules.signals.infrastructure import signal_store as store

MAX_ENTRIES = 100_000
_log = get_logger("zid.signals.propagation")
PROPAGATION = REGISTRY.histogram(
    "zid_revocation_propagation_seconds",
    "Commit of a restricting security state -> first authoritative observation by a PEP",
    ("state",),
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0),
)


def observed(principal_id: str, current: CurrentState | None, state: str) -> None:
    """Each authoritative read of a restricting state is logged with its commit -> observation
    lag (FR-SIG-003/004); cache hits are not observations."""
    if current is None or state == "NORMAL":
        return
    lag_ms = (datetime.now(UTC) - current.committed_at).total_seconds() * 1000
    PROPAGATION.observe(max(lag_ms, 0.0) / 1000, state=state)  # R3 SLO: p99 <= 2 s in-cell
    _log.info(
        "security state observed by PEP",
        extra={
            "component": "signals.propagation",
            "subject": principal_id,
            "state": state,
            "revision": current.revision,
            "commit_to_observation_ms": round(lag_ms, 1),
        },
    )


@dataclass(frozen=True, slots=True)
class _Entry:
    current: CurrentState | None
    revision: int
    fetched_at: datetime | None  # None: invalidated - a floor for the revision only


@dataclass(frozen=True, slots=True)
class StateView:
    state: str  # what the PDP sees: NORMAL, COMPROMISED, or a restricting state
    revision: int
    cached: bool


class SecurityStateCache:
    def __init__(self, max_entries: int = MAX_ENTRIES) -> None:
        self._entries: OrderedDict[tuple[str, str], _Entry] = OrderedDict()
        self._max = max_entries
        self.hits = self.misses = 0

    def get(
        self, tenant_id: str, subject_id: str, *, risk_class: int, now: datetime
    ) -> _Entry | None:
        entry = self._entries.get((tenant_id, subject_id))
        if entry is None or entry.fetched_at is None:
            return None
        if not cache_fresh(entry.fetched_at, now=now, risk_class=risk_class):
            return None
        return entry

    def put(
        self, tenant_id: str, subject_id: str, current: CurrentState | None, now: datetime
    ) -> bool:
        key = (tenant_id, subject_id)
        revision = current.revision if current else 0
        existing = self._entries.get(key)
        if existing is not None and existing.revision > revision:
            return False  # an older read never replaces a newer revision
        self._entries[key] = _Entry(current, revision, now)
        self._entries.move_to_end(key)
        while len(self._entries) > self._max:
            self._entries.popitem(last=False)
        return True

    def invalidate(self, tenant_id: str, subject_id: str, revision: int) -> None:
        """A newer revision was written: older entries stop being served at once."""
        key = (tenant_id, subject_id)
        existing = self._entries.get(key)
        if existing is None or existing.revision < revision:
            self._entries[key] = _Entry(None, revision, None)
            self._entries.move_to_end(key)

    def forget(self, tenant_id: str, subject_id: str) -> bool:
        """Drop everything cached about a subject (erasure, PRIV-10). Returns whether anything was held."""
        return self._entries.pop((tenant_id, subject_id), None) is not None

    def forget_tenant(self, tenant_id: str) -> int:
        """Drop everything cached for a tenant (deletion, TEN-25). Returns how many entries were held."""
        keys = [k for k in self._entries if k[0] == tenant_id]
        for k in keys:
            del self._entries[k]
        return len(keys)

    def clear(self) -> None:
        self._entries.clear()


CACHE = SecurityStateCache()  # one per process; R2/R3 never depend on it


async def security_view(
    conn: AsyncConnection | None,
    *,
    tenant_id: str,
    principal_id: str,
    auth_time: datetime | None,
    risk_class: int,
    now: datetime,
    cache: SecurityStateCache = CACHE,
) -> StateView:
    """The PDP's view of a principal's state, from the cache only where the risk class allows."""
    entry = cache.get(tenant_id, principal_id, risk_class=risk_class, now=now)
    if entry is not None:
        cache.hits += 1
        return StateView(pdp_state(entry.current, auth_time=auth_time), entry.revision, True)
    if conn is None:
        raise LookupError("no connection for an uncached security-state read")
    cache.misses += 1
    current = await store.read_state(conn, "PRINCIPAL", principal_id)
    cache.put(tenant_id, principal_id, current, now)
    state = pdp_state(current, auth_time=auth_time)
    observed(principal_id, current, state)
    return StateView(state, current.revision if current else 0, False)
