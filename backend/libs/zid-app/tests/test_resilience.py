"""Outbound call discipline (ZID-SVC-001 SVC-17 deadline, SVC-18 retry safety, SVC-19 circuit breaker)."""

from __future__ import annotations

import asyncio

import pytest
from zid_app.resilience import (
    Bulkhead,
    BulkheadFull,
    CircuitBreaker,
    CircuitOpen,
    DeadlineExceeded,
    budget_timeout,
    call_with_retry,
    outbound_deadline_header,
    remaining,
    reset_deadline,
    retry_safe,
    set_deadline_from_header,
)


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def run(coro):  # type: ignore[no-untyped-def]
    return asyncio.run(coro)


# ---------------------------------------------------------------- deadline (SVC-17)


def test_a_downstream_call_never_gets_more_time_than_its_caller_has_left() -> None:
    """SVC-17: the timeout is the call's own cap or what is left of the caller's budget, whichever is less, and the
    budget shrinks as time is spent, so a chain of calls cannot exceed the first caller's patience."""
    clock = Clock()
    token = set_deadline_from_header("2000", clock=clock)  # the caller has 2 s
    try:
        assert budget_timeout(5.0, clock) == pytest.approx(
            2.0
        )  # capped by the budget, not by the 5 s cap
        assert budget_timeout(1.0, clock) == 1.0  # its own smaller cap still applies
        clock.now += 1.5
        assert budget_timeout(5.0, clock) == pytest.approx(0.5)
        assert outbound_deadline_header(clock) == {
            "x-zid-deadline-ms": "500"
        }  # the next hop sees what is left
        clock.now += 0.6
        with pytest.raises(DeadlineExceeded):
            budget_timeout(5.0, clock)
    finally:
        reset_deadline(token)
    assert remaining(clock) is None  # the budget ends with the request
    assert budget_timeout(5.0, clock) == 5.0  # no caller deadline: the call's own cap


def test_a_missing_malformed_or_oversized_deadline_cannot_grant_unlimited_time() -> None:
    clock = Clock()
    for bad in (None, "", "abc", "-5", "0", "1.5"):
        token = set_deadline_from_header(bad, clock=clock)
        try:
            assert remaining(clock) is None
        finally:
            reset_deadline(token)
    token = set_deadline_from_header(
        "999999999", clock=clock
    )  # a caller cannot grant more than the ceiling
    try:
        assert remaining(clock) == pytest.approx(30.0)
    finally:
        reset_deadline(token)


def test_retries_stop_when_the_budget_cannot_cover_the_next_wait() -> None:
    """SVC-17: a retry that would wait past the deadline is not started."""
    clock = Clock()
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)
        clock.now += seconds

    calls: list[int] = []

    async def flaky() -> str:
        calls.append(1)
        raise ConnectionError("down")

    async def go() -> None:
        token = set_deadline_from_header("250", clock=clock)  # 250 ms: waits of 100 ms then 200 ms
        try:
            with pytest.raises(ConnectionError):
                await call_with_retry(
                    flaky, method="GET", attempts=5, sleep=fake_sleep, clock=clock
                )
        finally:
            reset_deadline(token)

    run(go())
    assert len(calls) == 2 and slept == [0.1]  # the second wait (200 ms) would pass the deadline


# ---------------------------------------------------------------- retry safety (SVC-18)


def test_a_non_idempotent_command_is_never_retried_without_an_idempotency_key() -> None:
    """SVC-18: POST and PATCH run once unless they carry an Idempotency-Key; reads, PUT and DELETE may repeat."""
    assert retry_safe("GET") and retry_safe("put") and retry_safe("DELETE") and retry_safe("HEAD")
    assert not retry_safe("POST") and not retry_safe("PATCH")
    assert not retry_safe("POST", {"Idempotency-Key": "  "})
    assert retry_safe("POST", {"idempotency-key": "k-1"}) and retry_safe(
        "PATCH", {"Idempotency-Key": "k-2"}
    )

    async def sleepless(_: float) -> None:
        return None

    def counted(failures: int):  # type: ignore[no-untyped-def]
        calls = {"n": 0}

        async def fn() -> str:
            calls["n"] += 1
            if calls["n"] <= failures:
                raise TimeoutError
            return "ok"

        return fn, calls

    fn, calls = counted(2)
    with pytest.raises(TimeoutError):
        run(call_with_retry(fn, method="POST", sleep=sleepless))  # one attempt, never repeated
    assert calls["n"] == 1
    fn, calls = counted(2)
    assert (
        run(call_with_retry(fn, method="POST", headers={"Idempotency-Key": "k"}, sleep=sleepless))
        == "ok"
    )
    assert calls["n"] == 3
    fn, calls = counted(9)
    with pytest.raises(TimeoutError):
        run(call_with_retry(fn, method="GET", attempts=4, sleep=sleepless))
    assert calls["n"] == 4  # attempts are bounded


def test_a_decision_not_to_call_is_not_retried() -> None:
    """SVC-18: an open circuit or a full bulkhead is an answer, not a transient failure to hammer."""
    calls = {"n": 0}

    async def fn() -> str:
        calls["n"] += 1
        raise CircuitOpen

    async def sleepless(_: float) -> None:
        return None

    with pytest.raises(CircuitOpen):
        run(call_with_retry(fn, method="GET", sleep=sleepless))
    assert calls["n"] == 1


# ---------------------------------------------------------------- circuit breaker and bulkhead (SVC-19)


def test_a_failing_dependency_is_cut_off_at_once_then_probed_and_closed_when_it_recovers() -> None:
    """SVC-19: after the threshold the breaker fails fast without calling; after the reset time one probe decides."""
    clock = Clock()
    breaker = CircuitBreaker(failure_threshold=3, reset_after=10, clock=clock)
    attempts = {"n": 0}
    healthy = {"up": False}

    async def dependency() -> str:
        attempts["n"] += 1
        if not healthy["up"]:
            raise ConnectionError("down")
        return "ok"

    async def go() -> None:
        for _ in range(3):
            with pytest.raises(ConnectionError):
                await breaker.call(dependency)
        assert breaker.state == "open"
        for _ in range(50):  # a flood is turned away without touching the dependency
            with pytest.raises(CircuitOpen):
                await breaker.call(dependency)
        assert attempts["n"] == 3
        clock.now += 11
        assert breaker.state == "half-open"
        with pytest.raises(
            ConnectionError
        ):  # the probe fails: open again for another full reset period
            await breaker.call(dependency)
        assert breaker.state == "open" and attempts["n"] == 4
        clock.now += 11
        healthy["up"] = True
        assert await breaker.call(dependency) == "ok"  # the probe succeeds: closed
        assert breaker.state == "closed" and await breaker.call(dependency) == "ok"

    run(go())
    with pytest.raises(ValueError):
        CircuitBreaker(failure_threshold=0)


def test_one_slow_dependency_cannot_take_every_worker() -> None:
    """SVC-19: only `limit` calls run at once; the rest are refused immediately instead of queueing."""
    bulkhead = Bulkhead(2)

    async def go() -> list[str]:
        gate = asyncio.Event()

        async def slow() -> str:
            await gate.wait()
            return "done"

        running = [asyncio.create_task(bulkhead.call(slow)) for _ in range(2)]
        await asyncio.sleep(0)
        refused = []
        for _ in range(5):
            with pytest.raises(BulkheadFull):
                await bulkhead.call(slow)
            refused.append("full")
        gate.set()
        results = await asyncio.gather(*running)
        assert (
            results == ["done", "done"] and await bulkhead.call(slow) == "done"
        )  # capacity is released
        return refused

    assert run(go()) == ["full"] * 5
    with pytest.raises(ValueError):
        Bulkhead(0)
