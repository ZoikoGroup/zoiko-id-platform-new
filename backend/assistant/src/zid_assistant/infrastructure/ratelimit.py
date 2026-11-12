"""In-process sliding-window rate limiter with a non-persistent salted key (ADR-12: no permanent fingerprint, RET-12 <= 24h).

Keys are hashed with a per-process random salt that is never stored, so counters cannot be turned into a tracking identifier.
A replica fleet should front this with the edge limiter (T1); this is the service's own floor.
"""

from __future__ import annotations

import hashlib
import secrets
import time
from collections import defaultdict, deque
from collections.abc import Callable

_MAX_KEYS = 50_000  # a spray of keys can never grow the process without bound


class RateLimiter:
    def __init__(self, window_seconds: int, clock: Callable[[], float] = time.monotonic) -> None:
        self._w = float(window_seconds)
        self._clock = clock
        self._salt = secrets.token_bytes(16)
        self._buckets: dict[str, dict[str, deque[float]]] = defaultdict(dict)

    def _k(self, raw: str) -> str:
        return hashlib.sha256(self._salt + raw.encode()).hexdigest()[:24]

    def allow(self, bucket: str, key: str, limit: int) -> tuple[bool, int]:
        """(allowed, retry_after_seconds). Records the hit only when allowed."""
        now = self._clock()
        b = self._buckets[bucket]
        if len(b) > _MAX_KEYS:
            for stale in [k for k, q in b.items() if not q or now - q[-1] > self._w][
                : _MAX_KEYS // 10
            ]:
                b.pop(stale, None)
        q = b.setdefault(self._k(key), deque())
        while q and now - q[0] >= self._w:
            q.popleft()
        if len(q) >= limit:
            return False, max(1, int(self._w - (now - q[0])))
        q.append(now)
        return True, 0
