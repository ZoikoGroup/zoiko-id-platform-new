"""Outbound call discipline: deadlines, safe retries, circuit breaking and bulkheads (ZID-SVC-001 §10, §12; SVC-17/18/19).

- Deadline: a request may carry `X-Zid-Deadline-Ms`, the time its caller still has. The inbound middleware turns that
  into an absolute budget for the request; every outbound call asks `budget_timeout(cap)` for its timeout, which is
  the smaller of the call's own cap and what is left, and raises `DeadlineExceeded` once nothing is left. A callee
  therefore never works past its caller's patience, and the budget shrinks as it is spent.
- Retry: `call_with_retry` repeats a call only when repeating is safe. A read, a PUT or a DELETE may be retried; a
  POST or PATCH only when it carries an `Idempotency-Key`; anything else runs exactly once. Retries stop when the
  budget cannot cover the next wait.
- Circuit breaker: after `failure_threshold` consecutive failures the breaker opens and calls fail at once with
  `CircuitOpen` until `reset_after` passes, when a single probe call decides whether it closes again.
- Bulkhead: at most `limit` calls to one dependency run at once; the next fails at once with `BulkheadFull`
  instead of queueing, so one slow dependency cannot take every worker or connection with it.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Mapping
from contextvars import ContextVar, Token
from typing import TypeVar

T = TypeVar("T")
DEADLINE_HEADER = "x-zid-deadline-ms"
MAX_BUDGET_S = 30.0
IDEMPOTENT_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "PUT", "DELETE"})

_deadline: ContextVar[float | None] = ContextVar("zid_deadline", default=None)


class DeadlineExceeded(Exception):
    """The caller's remaining time is used up."""


class CircuitOpen(Exception):
    """The dependency is being given time to recover; the call was not attempted."""


class BulkheadFull(Exception):
    """The dependency already has its limit of calls in flight; the call was not attempted."""


def set_deadline_from_header(
    value: str | None, *, clock: Callable[[], float] = time.monotonic
) -> Token[float | None]:
    """Start the request's budget from the caller's `X-Zid-Deadline-Ms`; an absent or malformed value sets none.
    The budget is capped at `MAX_BUDGET_S`, so a caller cannot grant a callee unlimited time."""
    deadline: float | None = None
    if value and value.isdigit() and int(value) > 0:
        deadline = clock() + min(int(value) / 1000.0, MAX_BUDGET_S)
    return _deadline.set(deadline)


def reset_deadline(token: Token[float | None]) -> None:
    _deadline.reset(token)


def remaining(clock: Callable[[], float] = time.monotonic) -> float | None:
    """Seconds left in this request's budget, or None when the caller set none."""
    deadline = _deadline.get()
    return None if deadline is None else deadline - clock()


def budget_timeout(cap: float, clock: Callable[[], float] = time.monotonic) -> float:
    """The timeout for one outbound call: its own cap, or what is left of the budget if that is less."""
    left = remaining(clock)
    if left is None:
        return cap
    if left <= 0:
        raise DeadlineExceeded
    return min(cap, left)


def outbound_deadline_header(clock: Callable[[], float] = time.monotonic) -> dict[str, str]:
    """The budget to pass to the next service, so the whole chain shares one deadline."""
    left = remaining(clock)
    return {} if left is None else {DEADLINE_HEADER: str(max(int(left * 1000), 1))}


def retry_safe(method: str, headers: Mapping[str, str] | None = None) -> bool:
    if method.upper() in IDEMPOTENT_METHODS:
        return True
    return bool(
        {k.lower(): v for k, v in (headers or {}).items()}.get("idempotency-key", "").strip()
    )


async def call_with_retry[T](
    fn: Callable[[], Awaitable[T]],
    *,
    method: str,
    headers: Mapping[str, str] | None = None,
    attempts: int = 3,
    base_delay: float = 0.1,
    retry_on: tuple[type[BaseException], ...] = (Exception,),
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> T:
    """Run `fn`, repeating after a failure only when repeating is safe and the budget covers the wait."""
    allowed = attempts if retry_safe(method, headers) else 1
    for attempt in range(1, allowed + 1):
        budget_timeout(1.0, clock)  # raises DeadlineExceeded when nothing is left
        try:
            return await fn()
        except (DeadlineExceeded, CircuitOpen, BulkheadFull):
            raise  # these are decisions, not transient failures
        except retry_on:
            if attempt == allowed:
                raise
            delay = base_delay * 2 ** (attempt - 1)
            left = remaining(clock)
            if left is not None and left <= delay:
                raise
            await sleep(delay)
    raise AssertionError("unreachable")  # pragma: no cover


class CircuitBreaker:
    def __init__(
        self,
        *,
        failure_threshold: int = 5,
        reset_after: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if failure_threshold < 1 or reset_after <= 0:
            raise ValueError("a breaker needs a positive threshold and reset time")
        self._threshold, self._reset, self._clock = failure_threshold, reset_after, clock
        self._failures = 0
        self._opened_at: float | None = None
        self._probing = False

    @property
    def state(self) -> str:
        if self._opened_at is None:
            return "closed"
        return "half-open" if self._clock() - self._opened_at >= self._reset else "open"

    async def call(self, fn: Callable[[], Awaitable[T]]) -> T:
        state = self.state
        if state == "open" or (state == "half-open" and self._probing):
            raise CircuitOpen
        if state == "half-open":
            self._probing = True  # exactly one probe decides
        try:
            result = await fn()
        except (DeadlineExceeded, CircuitOpen, BulkheadFull):
            self._probing = False
            raise
        except Exception:
            self._failures += 1
            if state == "half-open" or self._failures >= self._threshold:
                self._opened_at = self._clock()
            self._probing = False
            raise
        self._failures, self._opened_at, self._probing = 0, None, False
        return result


class Bulkhead:
    def __init__(self, limit: int) -> None:
        if limit < 1:
            raise ValueError("a bulkhead needs a positive limit")
        self._limit, self._in_flight = limit, 0

    async def call(self, fn: Callable[[], Awaitable[T]]) -> T:
        if self._in_flight >= self._limit:
            raise BulkheadFull
        self._in_flight += 1
        try:
            return await fn()
        finally:
            self._in_flight -= 1


__all__ = [
    "DEADLINE_HEADER",
    "Bulkhead",
    "BulkheadFull",
    "CircuitBreaker",
    "CircuitOpen",
    "DeadlineExceeded",
    "budget_timeout",
    "call_with_retry",
    "outbound_deadline_header",
    "remaining",
    "reset_deadline",
    "retry_safe",
    "set_deadline_from_header",
]
