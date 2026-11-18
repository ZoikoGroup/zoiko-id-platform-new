"""A failed first-party erasure is retried automatically, with a growing delay and a ceiling, and never reported complete unless the store really disposed the data (QA-023; CR-117), on PostgreSQL."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from conftest import ctx, engine
from onboarding_fixture import onboard
from sqlalchemy import text
from zid_db import tenant_transaction
from zid_platform.modules.evidence.application.runtime import configure_runtime, development_runtime
from zid_platform.modules.privacy.application import control
from zid_platform.modules.privacy.application import service as privacy
from zid_platform.modules.privacy.domain import control as rules

STORE = "dsr.flaky"


class Flaky:
    def __init__(self, failing: bool) -> None:
        self.failing, self.calls = failing, 0

    async def __call__(self, conn: Any, entry: Any, action: str) -> None:
        self.calls += 1
        if self.failing:
            raise RuntimeError("the store is down")


@pytest.fixture(autouse=True)
def runtime() -> Any:
    configure_runtime(development_runtime("uk-1"))
    yield
    configure_runtime(None)


def test_the_delay_doubles_from_fifteen_minutes_and_never_passes_a_day() -> None:
    assert [rules.retry_delay_minutes(n) for n in (1, 2, 3, 4, 5, 9)] == [
        15,
        30,
        60,
        120,
        240,
        1440,
    ]
    assert rules.MAX_ERASURE_ATTEMPTS == 5


def test_a_store_that_raised_is_retried_later_and_recovers_when_it_comes_back(rt_url: str) -> None:
    o = onboard(rt_url)
    subject = str(uuid.uuid4())
    flaky = Flaky(failing=True)
    privacy.register_store(STORE, disposer=flaky)
    t0 = datetime.now(UTC)

    async def run() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(o.tenant)) as c:
            await privacy.register_personal_data(
                c, o.tenant, subject_principal_id=subject, store_name=STORE, object_type="profile", object_ref="rec-1",
                purpose_code="ACCOUNT", data_category="D1", retention_policy_code=None, processor_id=None,
            )  # fmt: skip
            job = await control.open_erasure_job(
                c, o.tenant, o.owner, subject_ref=subject, scope="FIRST_PARTY_ONLY", reason="subject request", legal_basis="GDPR Art. 17"
            )  # fmt: skip
            task = next(t for t in job["tasks"] if t["target_ref"] == f"{STORE}:rec-1")
            assert (
                task["state"] == "FAILED" and task["failure"] == "DISPOSER_ERROR"
            )  # the failure is recorded, and the job did not crash
            assert job["state"] != "COMPLETE"
            first = flaky.calls

            # attempt 1 fails again: a delay is set
            r1 = await control.retry_failed_erasures(c, t0 + timedelta(minutes=1))
            assert r1["retried"] == 1 and r1["still_failing"] == 1 and flaky.calls == first + 1
            row = (
                await c.execute(
                    text(
                        "SELECT state, attempts, next_attempt_at FROM privacy.erasure_task WHERE task_id = :i"
                    ),
                    {"i": uuid.UUID(task["task_id"])},
                )
            ).one()
            assert (
                row.state == "FAILED"
                and row.attempts == 1
                and row.next_attempt_at == t0 + timedelta(minutes=1) + timedelta(minutes=15)
            )

            # not yet due: nothing is attempted
            assert (await control.retry_failed_erasures(c, t0 + timedelta(minutes=5)))[
                "retried"
            ] == 0

            # the store comes back; the next due attempt disposes and only then records a receipt
            flaky.failing = False
            r2 = await control.retry_failed_erasures(c, t0 + timedelta(minutes=30))
            assert r2["recovered"] == 1
            row = (
                await c.execute(
                    text(
                        "SELECT state, receipt_hash, failure FROM privacy.erasure_task WHERE task_id = :i"
                    ),
                    {"i": uuid.UUID(task["task_id"])},
                )
            ).one()
            assert row.state == "ACKNOWLEDGED" and row.receipt_hash and row.failure is None
            entry = (
                await c.execute(
                    text(
                        "SELECT status FROM privacy.data_map_entry WHERE store = :s AND object_ref = 'rec-1'"
                    ),
                    {"s": STORE},
                )
            ).scalar_one()
            assert entry == "DISPOSED"
            assert (await control.retry_failed_erasures(c, t0 + timedelta(days=2)))[
                "retried"
            ] == 0  # nothing left to retry

    asyncio.run(run())


def test_a_store_that_never_recovers_gives_up_after_the_ceiling_and_stays_failed(
    rt_url: str,
) -> None:
    o = onboard(rt_url)
    subject = str(uuid.uuid4())
    privacy.register_store(STORE, disposer=Flaky(failing=True))
    t0 = datetime.now(UTC)

    async def run() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(o.tenant)) as c:
            await privacy.register_personal_data(
                c, o.tenant, subject_principal_id=subject, store_name=STORE, object_type="profile", object_ref="rec-9",
                purpose_code="ACCOUNT", data_category="D1", retention_policy_code=None, processor_id=None,
            )  # fmt: skip
            await control.open_erasure_job(
                c,
                o.tenant,
                o.owner,
                subject_ref=subject,
                scope="FIRST_PARTY_ONLY",
                reason="subject request",
                legal_basis="GDPR Art. 17",
            )
            outcomes = []
            for day in range(1, 8):
                outcomes.append(await control.retry_failed_erasures(c, t0 + timedelta(days=day)))
            assert sum(r["retried"] for r in outcomes) == rules.MAX_ERASURE_ATTEMPTS
            assert sum(r["gave_up"] for r in outcomes) == 1
            row = (
                await c.execute(
                    text(
                        "SELECT state, attempts, failure, next_attempt_at FROM privacy.erasure_task WHERE target_ref = :r"
                    ),
                    {"r": f"{STORE}:rec-9"},
                )
            ).one()
            assert (
                row.state == "FAILED"
                and row.attempts == rules.MAX_ERASURE_ATTEMPTS
                and row.failure == "DISPOSER_ERROR"
                and row.next_attempt_at is None
            )
            stuck = (
                await c.execute(
                    text("SELECT count(*) FROM privacy.erasure_task WHERE state = 'FAILED'")
                )
            ).scalar_one()
            assert (
                stuck >= 1
            )  # it keeps showing as stuck for the oversight view until a person acts

    asyncio.run(run())
