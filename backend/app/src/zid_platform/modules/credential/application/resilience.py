"""Calling a source safely: retry with backoff, a rate limit and a circuit breaker (ZID-GAP-001 v2.0 GID-060, section 11 "Reliability").

A source is somebody else's system. Three rules keep a slow or failing one from hurting the platform or the person being checked:
- RETRY only what may succeed next time (a timeout, a dropped connection, an explicit "try again"), never a definitive answer, and
  never past a total time budget. Waits grow exponentially with a little jitter so retries from many checks do not arrive together.
  A lookup is a read, so retrying it is safe.
- RATE LIMIT per source (a token bucket): asking faster than the contract allows is refused here, before the source is bothered,
  and the check says SOURCE_UNAVAILABLE rather than failing the person.
- CIRCUIT BREAKER per source: after a run of failures the platform stops asking for a cooldown, so a source that is down is not hammered
  and checks fail fast. One probe is let through after the cooldown; success closes the circuit, failure reopens it.
Whatever happens, the outcome of a failed call is "the source could not be asked": never a verdict on the person, never a pass.
"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, TypeVar

T = TypeVar("T")


class TransientSourceError(Exception):
    """The source said (or behaved as if) "try again": a timeout, a dropped connection, a 429 or 503."""


class Fault(StrEnum):
    RATE_LIMITED = "SOURCE_RATE_LIMITED"  # our own limiter refused: the source was not called
    CIRCUIT_OPEN = "SOURCE_CIRCUIT_OPEN"  # the source has been failing: the source was not called
    RETRIES_EXHAUSTED = (
        "SOURCE_RETRIES_EXHAUSTED"  # transient failures until attempts or the time budget ran out
    )
    PERMANENT = "SOURCE_ERROR"  # a failure that retrying will not fix


RETRYABLE: tuple[type[BaseException], ...] = (TransientSourceError, TimeoutError, ConnectionError)


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int = 3
    base_delay: float = 0.25  # seconds before the second attempt
    factor: float = 2.0
    max_delay: float = 4.0
    jitter: float = 0.2  # +/- fraction of each delay
    budget: float = 10.0  # total seconds spent waiting between attempts

    def delay(self, attempt: int, rand: Callable[[], float] = random.random) -> float:
        """Wait before attempt number `attempt` + 1 (attempt counts from 1)."""
        raw = min(self.base_delay * (self.factor ** (attempt - 1)), self.max_delay)
        spread = raw * self.jitter
        return max(0.0, raw - spread + 2 * spread * rand())


@dataclass(slots=True)
class TokenBucket:
    """`capacity` calls at once, refilled at `per_second`. Time is passed in, so it is deterministic under test."""

    capacity: float
    per_second: float
    tokens: float = field(init=False)
    stamp: float | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        self.tokens = self.capacity

    def allow(self, now: float) -> bool:
        if self.stamp is not None:
            self.tokens = min(
                self.capacity, self.tokens + max(0.0, now - self.stamp) * self.per_second
            )
        self.stamp = now
        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True
        return False


@dataclass(slots=True)
class CircuitBreaker:
    failure_threshold: int = 5
    cooldown: float = 30.0
    failures: int = 0
    opened_at: float | None = None
    probing: bool = False

    def state(self, now: float) -> str:
        if self.opened_at is None:
            return "CLOSED"
        return "HALF_OPEN" if now - self.opened_at >= self.cooldown else "OPEN"

    def allow(self, now: float) -> bool:
        st = self.state(now)
        if st == "CLOSED":
            return True
        if st == "HALF_OPEN" and not self.probing:
            self.probing = True  # exactly one probe at a time
            return True
        return False

    def success(self) -> None:
        self.failures, self.opened_at, self.probing = 0, None, False

    def failure(self, now: float) -> None:
        self.failures += 1
        self.probing = False
        if self.failures >= self.failure_threshold or self.opened_at is not None:
            self.opened_at = now  # (re)open: a failed probe restarts the cooldown


@dataclass(frozen=True, slots=True)
class Outcome[T]:
    value: T | None
    attempts: int
    fault: Fault | None = None
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.fault is None


class Resilience:
    """The policy, one limiter and one breaker per source id. Hold ONE instance per process so the state is shared."""

    def __init__(
        self,
        policy: RetryPolicy | None = None,
        *,
        rate_capacity: float = 20.0,
        rate_per_second: float = 10.0,
        failure_threshold: int = 5,
        cooldown: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        rand: Callable[[], float] = random.random,
    ) -> None:
        self.policy = policy or RetryPolicy()
        self._rate = (rate_capacity, rate_per_second)
        self._breaker = (failure_threshold, cooldown)
        self._clock, self._sleep, self._rand = clock, sleep, rand
        self._limiters: dict[str, TokenBucket] = {}
        self._breakers: dict[str, CircuitBreaker] = {}

    def breaker(self, source_id: str) -> CircuitBreaker:
        return self._breakers.setdefault(source_id, CircuitBreaker(*self._breaker))

    def limiter(self, source_id: str) -> TokenBucket:
        return self._limiters.setdefault(source_id, TokenBucket(*self._rate))

    async def call(self, source_id: str, fn: Callable[[], Awaitable[T]]) -> Outcome[T]:
        """Ask the source with the policy. Never raises for a source fault: the fault is the outcome."""
        breaker = self.breaker(source_id)
        if not breaker.allow(self._clock()):
            return Outcome(None, 0, Fault.CIRCUIT_OPEN)
        waited, attempts = 0.0, 0
        while True:
            if not self.limiter(source_id).allow(self._clock()):
                if breaker.probing:
                    breaker.probing = False  # a refused probe is not a verdict on the source
                return Outcome(None, attempts, Fault.RATE_LIMITED)
            attempts += 1
            try:
                value = await fn()
            except RETRYABLE as e:
                breaker.failure(self._clock())
                wait = self.policy.delay(attempts, self._rand)
                if (
                    attempts >= self.policy.max_attempts
                    or waited + wait > self.policy.budget
                    or not breaker.allow(self._clock())
                ):
                    return Outcome(None, attempts, Fault.RETRIES_EXHAUSTED, type(e).__name__)
                waited += wait
                await self._sleep(wait)
                continue
            except Exception as e:  # noqa: BLE001 - any other failure is permanent for this call
                breaker.failure(self._clock())
                return Outcome(None, attempts, Fault.PERMANENT, type(e).__name__)
            breaker.success()
            return Outcome(value, attempts)


def describe(outcome: Outcome[Any]) -> dict[str, Any]:
    """What goes into the stored assertion about a failed call: a code and a count, never an exception message."""
    return {"fault": outcome.fault.value if outcome.fault else None, "attempts": outcome.attempts}
