"""Short-lived dynamic credentials with leases (ZID-CRYPTO-001 §15; CRYP-25).

A database or workload credential that the secrets broker mints on demand carries a lease. This module keeps
exactly one live lease per consumer and never serves a stale one:

- `current()` returns the lease while it has more than `renew_margin` of its lifetime left, and otherwise asks
  the broker for a new one, so an expiring credential is replaced before it stops working.
- `use(fn)` runs `fn(lease)`; if the consumer reports the credential was refused (`LeaseRejected`: revoked,
  expired, rotated underneath us) the lease is dropped and `fn` is retried once with a fresh lease. A rejected
  lease is never presented a second time.
- `revoke()` ends the lease at the broker and forgets it; the next use obtains a new one.
Values are held in memory only and never appear in an error message.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, TypeVar

from zid_crypto import CryptoError

T = TypeVar("T")


class LeaseRejected(CryptoError):
    """The consumer (a database, a service) refused the leased credential."""

    def __init__(self, detail: str = "") -> None:
        super().__init__("LEASE_REJECTED", detail)


@dataclass(frozen=True, slots=True)
class Lease:
    lease_id: str
    ttl_seconds: float
    issued_at: float  # on the manager's clock
    values: Mapping[str, str] = field(repr=False)  # for example {"username": ..., "password": ...}

    def remaining(self, now: float) -> float:
        return self.issued_at + self.ttl_seconds - now


class LeasedCredential:
    def __init__(
        self,
        acquire: Callable[[], Lease],
        *,
        revoke: Callable[[str], None] | None = None,
        renew_margin: float = 0.2,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not 0 < renew_margin < 1:
            raise ValueError("renew_margin is a fraction of the lease lifetime, between 0 and 1")
        self._acquire, self._revoke = acquire, revoke
        self._margin, self._clock = renew_margin, clock
        self._lease: Lease | None = None

    def current(self) -> Lease:
        now = self._clock()
        lease = self._lease
        if lease is None or lease.remaining(now) <= lease.ttl_seconds * self._margin:
            lease = self._lease = self._acquire()
        return lease

    def use(self, fn: Callable[[Lease], T]) -> T:
        lease = self.current()
        try:
            return fn(lease)
        except LeaseRejected:
            self._lease = None  # never present a refused lease again
            return fn(self.current())

    def revoke(self) -> None:
        lease, self._lease = self._lease, None
        if lease is not None and self._revoke is not None:
            self._revoke(lease.lease_id)


def openbao_lease_source(
    broker: Any, path: str, *, clock: Callable[[], float] = time.monotonic
) -> tuple[Callable[[], Lease], Callable[[str], None]]:
    """`(acquire, revoke)` for a dynamic secrets engine path such as `database/creds/zid-app`."""
    return (lambda: broker.lease(path, clock=clock)), broker.revoke_lease


__all__ = ["Lease", "LeaseRejected", "LeasedCredential", "openbao_lease_source"]
