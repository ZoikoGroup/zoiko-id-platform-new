"""A small limit on failed credential exchanges (ZID-EXT-IDV-001 SEC-04, "API abuse"): too many wrong secrets for one client id in a short time answer RATE_LIMITED, so a secret cannot be guessed
by trying. It counts failures only, so a partner whose integration works is never slowed. The count lives in this process: with several replicas each keeps its own, which is a floor, not a
promise; a shared counter belongs with the rate limiting the platform already runs for everything else."""

from __future__ import annotations

import time
from collections import OrderedDict, deque

WINDOW_SECONDS = 300
MAX_FAILURES = 10
MAX_KEYS = 10_000  # bound on memory: the oldest keys are forgotten first


class FailureLimiter:
    def __init__(
        self, window: int = WINDOW_SECONDS, limit: int = MAX_FAILURES, keys: int = MAX_KEYS
    ) -> None:
        self._window = window
        self._limit = limit
        self._keys = keys
        self._seen: OrderedDict[str, deque[float]] = OrderedDict()

    def _prune(self, key: str, now: float) -> deque[float]:
        q = self._seen.get(key)
        if q is None:
            return deque()
        while q and now - q[0] > self._window:
            q.popleft()
        return q

    def blocked(self, key: str, now: float | None = None) -> int:
        """Seconds until another attempt is allowed, or 0."""
        now = time.monotonic() if now is None else now
        q = self._prune(key, now)
        if len(q) < self._limit:
            return 0
        return max(1, int(self._window - (now - q[0])))

    def failed(self, key: str, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        q = self._prune(key, now)
        q.append(now)
        self._seen[key] = q
        self._seen.move_to_end(key)
        while len(self._seen) > self._keys:
            self._seen.popitem(last=False)

    def succeeded(self, key: str) -> None:
        self._seen.pop(key, None)
