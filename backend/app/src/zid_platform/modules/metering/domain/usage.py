"""API usage metering and the per-tenant rate limit (ZID-UX-001 §18; CR-023). Pure domain, no I/O.

- A call is counted by tenant, hour, API surface, OAuth client and status class. Never by path with ids,
  query string, body or person: usage says how much and how it answered, not who asked what.
- The rate limit is a sliding window per tenant, so one tenant's burst cannot starve the others and the
  caller always learns when it may try again (`Retry-After`). Tenants cannot raise it.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime

# First matching prefix wins; the surface is a coarse label, never the raw path.
SURFACES: tuple[tuple[str, str], ...] = (
    ("/v1/webhooks", "webhooks"),
    ("/v1/alerts", "alerts"),
    ("/v1/search", "search"),
    ("/v1/settings", "settings"),
    ("/v1/usage", "usage"),
    ("/v1/machine", "agents"),
    ("/v1/authority-grants", "authority"),
    ("/v1/approval-requests", "authority"),
    ("/v1/mandates", "mandates"),
    ("/v1/mandate-reservations", "mandates"),
    ("/v1/policy", "policy"),
    ("/v1/evidence", "evidence"),
    ("/v1/privacy", "privacy"),
    ("/v1/integration", "integration"),
    ("/v1/onboarding", "onboarding"),
    ("/v1/tenant", "tenant"),
    ("/access/v1", "authorization"),
    ("/ssf", "signals"),
    ("/signals", "signals"),
    ("/security-state", "signals"),
    ("/credential", "credentials"),
    ("/presentations", "credentials"),
    ("/verification", "verification"),
    ("/principals", "verification"),
    ("/provisioning", "provisioning"),
    ("/scim", "provisioning"),
    ("/federation", "federation"),
    ("/oauth2", "oauth"),
    ("/sessions", "sessions"),
    ("/authn", "authentication"),
    ("/admin", "administration"),
)


def surface_for(path: str) -> str:
    for prefix, surface in SURFACES:
        if path == prefix or path.startswith((prefix + "/", prefix + ":")):
            return surface
    return "other"


def status_class(code: int) -> str:
    return f"{min(max(code // 100, 2), 5)}xx"


@dataclass(frozen=True, slots=True)
class UsageKey:
    tenant_id: str
    bucket: datetime  # the hour, UTC
    surface: str
    client_id: str
    status_class: str


class UsageBuffer:
    """Counts waiting to be written. Bounded: past `limit` distinct keys new keys are dropped, never memory."""

    def __init__(self, limit: int = 5000) -> None:
        self._counts: dict[UsageKey, int] = {}
        self._limit = limit

    def add(self, key: UsageKey, n: int = 1) -> None:
        if key not in self._counts and len(self._counts) >= self._limit:
            return
        self._counts[key] = self._counts.get(key, 0) + n

    def drain(self) -> dict[UsageKey, int]:
        out, self._counts = self._counts, {}
        return out

    def __len__(self) -> int:
        return len(self._counts)


def hour_of(now: datetime) -> datetime:
    return now.replace(minute=0, second=0, microsecond=0)


@dataclass(frozen=True, slots=True)
class Verdict:
    allowed: bool
    used: int
    limit: int
    retry_after_s: int


class RateLimiter:
    """Sliding-window limiter, one window per key (the tenant). `limit <= 0` means unlimited."""

    def __init__(self, limit: int, window_s: int = 60) -> None:
        self.limit, self.window_s = limit, window_s
        self._hits: dict[str, deque[float]] = {}

    def _trim(self, key: str, now: float) -> deque[float]:
        q = self._hits.setdefault(key, deque())
        while q and now - q[0] >= self.window_s:
            q.popleft()
        return q

    def check(self, key: str, now: float) -> Verdict:
        """Count this call and say whether it is allowed. A refused call does not extend the window."""
        if self.limit <= 0:
            return Verdict(True, 0, 0, 0)
        q = self._trim(key, now)
        if len(q) >= self.limit:
            return Verdict(False, len(q), self.limit, max(1, int(self.window_s - (now - q[0])) + 1))
        q.append(now)
        return Verdict(True, len(q), self.limit, 0)

    def peek(self, key: str, now: float) -> Verdict:
        """Where a key stands, without counting a call."""
        if self.limit <= 0:
            return Verdict(True, 0, 0, 0)
        q = self._trim(key, now)
        reset = max(0, int(self.window_s - (now - q[0])) + 1) if q else 0
        return Verdict(len(q) < self.limit, len(q), self.limit, reset)
