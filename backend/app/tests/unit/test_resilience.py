"""GID-060 calling a source safely: retry with backoff, a rate limit and a circuit breaker. Deterministic: no real time, no sleeping."""

from __future__ import annotations

import asyncio

import pytest
from zid_platform.modules.credential.application import resilience as rs


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def make(clock: Clock, **kw) -> rs.Resilience:
    kw.setdefault("rand", lambda: 0.5)  # zero net jitter: delays are exactly base * factor^n
    return rs.Resilience(clock=clock, sleep=clock.sleep, **kw)


class Flaky:
    """Fails `failures` times with `exc`, then answers."""

    def __init__(self, failures: int, exc: BaseException | None = None) -> None:
        self.failures, self.exc, self.calls = failures, exc or rs.TransientSourceError("503"), 0

    async def __call__(self) -> str:
        self.calls += 1
        if self.calls <= self.failures:
            raise self.exc
        return "answer"


def run(coro):
    return asyncio.run(coro)


def test_a_first_try_success_makes_one_attempt_and_waits_for_nothing():
    c = Clock()
    out = run(make(c).call("s", Flaky(0)))
    assert out.ok and out.value == "answer" and out.attempts == 1 and c.slept == []


def test_a_transient_failure_is_retried_with_exponentially_growing_waits():
    c = Clock()
    f = Flaky(2)
    out = run(make(c).call("s", f))
    assert out.ok and out.attempts == 3 and f.calls == 3
    assert c.slept == [0.25, 0.5]  # base * 2^(n-1), jitter centered


def test_attempts_run_out_into_a_fault_not_an_exception():
    c = Clock()
    f = Flaky(10)
    out = run(make(c).call("s", f))
    assert out.fault is rs.Fault.RETRIES_EXHAUSTED and out.attempts == 3 and f.calls == 3
    assert out.detail == "TransientSourceError" and len(c.slept) == 2


@pytest.mark.parametrize("exc", [TimeoutError("slow"), ConnectionError("reset")])
def test_timeouts_and_dropped_connections_are_retried_too(exc):
    out = run(make(Clock()).call("s", Flaky(1, exc)))
    assert out.ok and out.attempts == 2


def test_a_definitive_failure_is_never_retried():
    c = Clock()
    f = Flaky(10, ValueError("bad request"))
    out = run(make(c).call("s", f))
    assert out.fault is rs.Fault.PERMANENT and out.attempts == 1 and f.calls == 1 and c.slept == []


def test_the_wait_budget_stops_retries_early():
    c = Clock()
    r = make(
        c,
        policy=rs.RetryPolicy(
            max_attempts=10, base_delay=1.0, factor=2.0, max_delay=100, budget=2.5
        ),
    )
    out = run(r.call("s", Flaky(100)))
    assert out.fault is rs.Fault.RETRIES_EXHAUSTED and sum(c.slept) <= 2.5 and out.attempts < 10


def test_delays_are_capped_and_jittered_within_bounds():
    p = rs.RetryPolicy(base_delay=1.0, factor=10.0, max_delay=4.0, jitter=0.2)
    assert p.delay(1, lambda: 0.5) == 1.0 and p.delay(3, lambda: 0.5) == 4.0  # capped
    assert p.delay(1, lambda: 0.0) == pytest.approx(0.8) and p.delay(
        1, lambda: 1.0
    ) == pytest.approx(1.2)
    assert all(0.0 <= p.delay(n) <= 4.8 for n in range(1, 8))


# --- rate limit ----------------------------------------------------------------------------------------------------


def test_the_token_bucket_allows_a_burst_then_refills_with_time():
    b = rs.TokenBucket(capacity=2, per_second=1)
    assert [b.allow(0.0), b.allow(0.0), b.allow(0.0)] == [True, True, False]
    assert b.allow(1.0) is True and b.allow(1.0) is False
    assert b.allow(100.0) and b.allow(100.0) and not b.allow(100.0)  # refill is capped at capacity


def test_a_rate_limited_call_never_reaches_the_source():
    c = Clock()
    r = make(c, rate_capacity=1, rate_per_second=0.0001)
    f = Flaky(0)
    first, second = run(r.call("s", f)), run(r.call("s", f))
    assert (
        first.ok and second.fault is rs.Fault.RATE_LIMITED and f.calls == 1 and second.attempts == 0
    )


# --- circuit breaker -----------------------------------------------------------------------------------------------


def test_the_circuit_opens_after_repeated_failures_and_then_fails_fast_without_calling():
    c = Clock()
    r = make(c, failure_threshold=3, cooldown=30.0, policy=rs.RetryPolicy(max_attempts=1))
    f = Flaky(100)
    for _ in range(3):
        assert run(r.call("s", f)).fault is rs.Fault.RETRIES_EXHAUSTED
    calls = f.calls
    out = run(r.call("s", f))
    assert out.fault is rs.Fault.CIRCUIT_OPEN and out.attempts == 0 and f.calls == calls
    assert r.breaker("s").state(c()) == "OPEN"


def test_after_the_cooldown_one_probe_decides_and_success_closes_the_circuit():
    c = Clock()
    r = make(c, failure_threshold=2, cooldown=30.0, policy=rs.RetryPolicy(max_attempts=1))
    f = Flaky(2)  # fails twice, then recovers
    run(r.call("s", f)), run(r.call("s", f))
    assert run(r.call("s", f)).fault is rs.Fault.CIRCUIT_OPEN
    c.now += 30.0
    assert r.breaker("s").state(c()) == "HALF_OPEN"
    probe = run(r.call("s", f))
    assert probe.ok and r.breaker("s").state(c()) == "CLOSED"
    assert run(r.call("s", f)).ok


def test_a_failed_probe_reopens_the_circuit_and_restarts_the_cooldown():
    c = Clock()
    r = make(c, failure_threshold=1, cooldown=30.0, policy=rs.RetryPolicy(max_attempts=1))
    f = Flaky(100)
    run(r.call("s", f))
    c.now += 30.0
    assert run(r.call("s", f)).fault is rs.Fault.RETRIES_EXHAUSTED  # the probe failed
    assert run(r.call("s", f)).fault is rs.Fault.CIRCUIT_OPEN  # and the cooldown started again
    c.now += 29.0
    assert run(r.call("s", f)).fault is rs.Fault.CIRCUIT_OPEN
    c.now += 1.0
    assert r.breaker("s").state(c()) == "HALF_OPEN"


def test_only_one_probe_is_let_through_at_a_time():
    b = rs.CircuitBreaker(failure_threshold=1, cooldown=10.0)
    b.failure(0.0)
    assert [b.allow(5.0), b.allow(10.0), b.allow(10.0)] == [False, True, False]


def test_one_sick_source_does_not_stop_another():
    c = Clock()
    r = make(c, failure_threshold=1, policy=rs.RetryPolicy(max_attempts=1))
    run(r.call("down", Flaky(100)))
    assert run(r.call("down", Flaky(0))).fault is rs.Fault.CIRCUIT_OPEN
    assert run(r.call("healthy", Flaky(0))).ok


def test_a_retry_that_trips_the_breaker_stops_retrying():
    c = Clock()
    r = make(c, failure_threshold=2, policy=rs.RetryPolicy(max_attempts=10))
    f = Flaky(100)
    out = run(r.call("s", f))
    assert (
        out.fault is rs.Fault.RETRIES_EXHAUSTED and f.calls == 2
    )  # the second failure opened the circuit: no third try


def test_the_stored_description_has_a_code_and_a_count_but_never_the_exception_text():
    c = Clock()
    out = run(
        make(c).call("s", Flaky(10, rs.TransientSourceError("secret token abc123 in the message")))
    )
    d = rs.describe(out)
    assert d == {"fault": "SOURCE_RETRIES_EXHAUSTED", "attempts": 3} and "abc123" not in str(d)
    assert rs.describe(run(make(c).call("ok", Flaky(0)))) == {"fault": None, "attempts": 1}
