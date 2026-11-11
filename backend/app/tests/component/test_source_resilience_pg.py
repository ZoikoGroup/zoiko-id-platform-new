"""GID-060 through the claim check: a flaky source is retried, a dead one is not hammered, and none of it is a verdict on the person."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

from conftest import ctx, engine
from sqlalchemy import text
from zid_db import tenant_transaction
from zid_platform.modules.credential.application import claim_checks as checks
from zid_platform.modules.credential.application import resilience as rs
from zid_platform.modules.credential.infrastructure.registries import (
    issuer_registry,
    source_contracts,
)
from zid_platform.modules.verification.ports import pack_registry

NOW = datetime(2026, 11, 11, 12, tzinfo=UTC)
LIC = {
    "license_ref": "L-1",
    "regulator": "Sample State Licensing Board",
    "activity_scope": "legal.advice",
}


def uid() -> str:
    return str(uuid.uuid4())


class Sleepless:
    def __init__(self) -> None:
        self.t, self.slept = 0.0, []

    def clock(self) -> float:
        return self.t

    async def sleep(self, s: float) -> None:
        self.slept.append(s)
        self.t += s


class FlakySim(checks.SimulatorSource):
    """The simulator, failing `failures` times first."""

    def __init__(self, failures: int, exc: BaseException | None = None) -> None:
        super().__init__("test")
        self.failures, self.calls, self.exc = (
            failures,
            0,
            exc or rs.TransientSourceError("503 try again"),
        )

    async def lookup(self, *a, **k):
        self.calls += 1
        if self.calls <= self.failures:
            raise self.exc
        return await super().lookup(*a, **k)


def resilience(s: Sleepless, **kw) -> rs.Resilience:
    return rs.Resilience(clock=s.clock, sleep=s.sleep, rand=lambda: 0.5, **kw)


async def check(c, tenant, subject, source, res):
    return await checks.run_check(
        c, tenant_id=tenant, subject_principal_id=subject, kind="PROFESSIONAL_LICENSE", claimed=LIC,
        jurisdiction={"country": "US", "subdivision": "CA"}, profession=None, packs=pack_registry(), source=source,
        now=NOW, environment="test", gate=checks.Gate(registry=issuer_registry(), contracts=source_contracts(), resilience=res),
    )  # fmt: skip


def run(coro):
    return asyncio.run(coro)


def test_a_transient_failure_is_retried_and_the_check_still_verifies(rt_url: str) -> None:
    tenant, subject, s = uid(), uid(), Sleepless()
    src = FlakySim(2)

    async def go() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            r = await check(c, tenant, subject, src, resilience(s))
            assert r.assertion.decision.value == "VERIFIED" and src.calls == 3
            assert s.slept == [0.25, 0.5] and "source_fault" not in r.to_contract()

    run(go())


def test_a_source_that_stays_down_is_unavailable_with_a_code_and_a_count_never_the_error_text(
    rt_url: str,
) -> None:
    tenant, subject, s = uid(), uid(), Sleepless()
    src = FlakySim(100, rs.TransientSourceError("secret-token-abc123 upstream said no"))

    async def go() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            r = await check(c, tenant, subject, src, resilience(s))
            k = r.to_contract()
            assert (
                r.assertion.decision.value == "SOURCE_UNAVAILABLE"
                and r.assertion.primary_source_verified is False
            )
            assert k["source_fault"] == {"fault": "SOURCE_RETRIES_EXHAUSTED", "attempts": 3}
            stored = (
                await c.execute(
                    text("SELECT assertion::text, source_reference FROM cred.claim_check")
                )
            ).one()
            assert "abc123" not in stored[0] and stored[1] == "SOURCE_RETRIES_EXHAUSTED"

    run(go())


def test_a_circuit_that_has_opened_stops_asking_the_source_at_all(rt_url: str) -> None:
    tenant, subject, s = uid(), uid(), Sleepless()
    src = FlakySim(1000)
    res = resilience(s, failure_threshold=3, cooldown=60.0, policy=rs.RetryPolicy(max_attempts=1))

    async def go() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            for _ in range(3):
                await check(c, tenant, uid(), src, res)
            calls = src.calls
            r = await check(c, tenant, subject, src, res)
            assert src.calls == calls  # the source was not asked
            assert r.to_contract()["source_fault"] == {
                "fault": "SOURCE_CIRCUIT_OPEN",
                "attempts": 0,
            }
            assert r.assertion.decision.value == "SOURCE_UNAVAILABLE"
            s.t += 60.0  # after the cooldown one probe goes through, and a healthy source closes the circuit
            src.failures = 0
            ok = await check(c, tenant, uid(), src, res)
            assert (
                ok.assertion.decision.value == "VERIFIED"
                and res.breaker(src.source_id).state(s.t) == "CLOSED"
            )

    run(go())


def test_the_rate_limit_refuses_before_the_source_is_bothered(rt_url: str) -> None:
    tenant, s = uid(), Sleepless()
    src = FlakySim(0)
    res = resilience(s, rate_capacity=1, rate_per_second=0.0001)

    async def go() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            first = await check(c, tenant, uid(), src, res)
            second = await check(c, tenant, uid(), src, res)
            assert first.assertion.decision.value == "VERIFIED" and src.calls == 1
            assert (
                second.assertion.decision.value == "SOURCE_UNAVAILABLE"
                and second.to_contract()["source_fault"]["fault"] == "SOURCE_RATE_LIMITED"
            )

    run(go())


def test_without_a_policy_one_failure_is_still_just_unavailable(rt_url: str) -> None:
    tenant, subject = uid(), uid()
    src = FlakySim(1, ValueError("boom"))

    async def go() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            r = await check(c, tenant, subject, src, None)
            assert r.assertion.decision.value == "SOURCE_UNAVAILABLE" and r.to_contract()[
                "source_fault"
            ] == {"fault": "SOURCE_ERROR", "attempts": 1}

    run(go())


def test_t32_an_unavailable_source_never_closes_the_door_on_an_appeal(rt_url: str) -> None:
    """ZID-INT-002 T32: when the source cannot be asked, the person can still appeal and the check is never edited."""
    from zid_platform.modules.credential.application import trust_service as svc

    tenant, subject, s = uid(), uid(), Sleepless()
    src = FlakySim(100)

    async def go() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            r = await check(c, tenant, subject, src, resilience(s))
            assert r.assertion.decision.value == "SOURCE_UNAVAILABLE"
            before = (
                await c.execute(text("SELECT assertion::text FROM cred.claim_check"))
            ).scalar_one()
            appeal = await svc.raise_appeal(
                c,
                tenant_id=tenant,
                check_id=r.check_id,
                raised_by=subject,
                reason="the register was down",
                correction={},
            )
            assert appeal
            after = (
                await c.execute(text("SELECT assertion::text FROM cred.claim_check"))
            ).scalar_one()
            assert before == after, "an appeal never edits the check it is about"

    run(go())
