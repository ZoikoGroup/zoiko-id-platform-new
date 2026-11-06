"""Scheduled integrity verification and required external timestamps (EVID-06/16/25), against PostgreSQL.

Tampering is only defensible if it is *found*: the worker re-verifies each partition on a schedule, raises one security incident when it
fails, and a deployment can require an external timestamp before anything counts as sealed.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from conftest import ctx, engine
from sqlalchemy import text
from zid_db import tenant_transaction
from zid_evidence import Digest
from zid_platform.modules.evidence.application import ledger
from zid_platform.modules.evidence.application.integrity import IntegrityState, verify_due
from zid_platform.modules.evidence.application.runtime import (
    EvidenceRuntime,
    configure_runtime,
    development_runtime,
)
from zid_platform.modules.evidence.application.worker import worker_pass
from zid_platform.modules.evidence.domain.ledger import Partition
from zid_platform.modules.evidence.infrastructure import ledger_store as store
from zid_platform.modules.evidence.infrastructure.tsa import TimestampError

INCIDENT = "com.zoiko.id.evidence.integrity.failed.v1"


def uid() -> str:
    return str(uuid.uuid4())


@pytest.fixture(autouse=True)
def fresh_runtime() -> Any:
    rt = development_runtime("uk-1")
    configure_runtime(rt)
    yield rt
    configure_runtime(None)


async def three_records(rt_url: str, tenant: str) -> None:
    async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
        for i in range(3):
            await ledger.append(
                c,
                tenant_id=tenant,
                record_type="test.record",
                record_id=f"r{i}",
                record={"n": i},
                evidence_class="E2",
                cell_id="uk-1",
            )


async def everyone_else_verified(
    owner_url: str, pid: str, state: IntegrityState, now: datetime
) -> None:
    """The database is shared with other tests, some of which tamper on purpose: mark every other partition as just verified."""
    async with engine(owner_url) as eng, eng.begin() as c, store.platform_scope(c):
        for other in await store.partitions_with_entries(c):
            if other != pid:
                state.last_verified[other] = now


async def tamper(owner_url: str, pid: str) -> None:
    async with engine(owner_url) as eng, eng.begin() as c:
        await c.execute(
            text("ALTER TABLE evidence.ledger_entry DISABLE TRIGGER ledger_entry_append_only")
        )
        await c.execute(
            text(
                "UPDATE evidence.ledger_entry SET record_hash = :h WHERE partition_id = :p AND sequence_no = 2"
            ),
            {"h": "sha-256:" + "1" * 64, "p": pid},
        )
        await c.execute(
            text("ALTER TABLE evidence.ledger_entry ENABLE TRIGGER ledger_entry_append_only")
        )


async def incidents(owner_url: str, tenant: str) -> int:
    async with engine(owner_url) as eng, eng.begin() as c:
        return int(
            (
                await c.execute(
                    text(
                        "SELECT count(*) FROM events.outbox_event WHERE tenant_id = :t AND event_type = :e"
                    ),
                    {"t": uuid.UUID(tenant), "e": INCIDENT},
                )
            ).scalar_one()
        )


def test_the_worker_finds_tampering_by_itself_and_raises_one_incident(
    rt_url: str, owner_url: str, fresh_runtime: EvidenceRuntime
) -> None:
    tenant = uid()
    pid = Partition(tenant, "uk-1").id
    now = datetime.now(UTC)

    async def run() -> None:
        await three_records(rt_url, tenant)
        async with engine(owner_url) as eng, eng.begin() as c:
            from zid_platform.modules.evidence.application.checkpointer import seal_partition

            await seal_partition(c, pid, signer=fresh_runtime.signer)
        state = IntegrityState()
        await everyone_else_verified(owner_url, pid, state, now + timedelta(days=30))

        async def check(at: datetime) -> Any:
            async with engine(owner_url) as eng, eng.begin() as c:
                return await verify_due(
                    c, rt=fresh_runtime, now=at, interval=timedelta(hours=1), batch=5, state=state
                )

        first = await check(now)
        assert first.verified == [pid] and not first.failed
        assert (await check(now + timedelta(minutes=10))).verified == []  # not due again yet
        assert (await check(now + timedelta(hours=2))).verified == [pid]  # due again, still clean
        assert await incidents(owner_url, tenant) == 0

        await tamper(owner_url, pid)
        found = await check(now + timedelta(hours=4))
        assert found.failed == [pid] and not found.verified
        assert pid in state.failing
        assert await incidents(owner_url, tenant) == 1
        await check(now + timedelta(hours=6))  # still failing: reported, but no second incident
        assert await incidents(owner_url, tenant) == 1

    asyncio.run(run())


def test_the_batch_limits_how_many_partitions_one_pass_checks(
    rt_url: str, owner_url: str, fresh_runtime: EvidenceRuntime
) -> None:
    tenants = [uid() for _ in range(3)]
    now = datetime.now(UTC)

    async def run() -> None:
        for t in tenants:
            await three_records(rt_url, t)
        mine = {Partition(t, "uk-1").id for t in tenants}
        state = IntegrityState()
        async with engine(owner_url) as eng, eng.begin() as c, store.platform_scope(c):
            for other in await store.partitions_with_entries(c):
                if other not in mine:
                    state.last_verified[other] = now + timedelta(days=30)
        done: set[str] = set()
        for step in range(3):
            async with engine(owner_url) as eng, eng.begin() as c:
                got = await verify_due(
                    c,
                    rt=fresh_runtime,
                    now=now + timedelta(seconds=step),
                    interval=timedelta(hours=1),
                    batch=1,
                    state=state,
                )
            assert len(got.verified) == 1  # one partition per pass, the longest-waiting first
            done |= set(got.verified)
        assert done == mine

    asyncio.run(run())


def test_the_worker_pass_runs_the_scheduled_check(
    rt_url: str, owner_url: str, fresh_runtime: EvidenceRuntime
) -> None:
    tenant = uid()
    pid = Partition(tenant, "uk-1").id

    async def run() -> None:
        await three_records(rt_url, tenant)
        async with engine(owner_url) as eng, eng.begin() as c:
            report = await worker_pass(
                c,
                rt=fresh_runtime,
                force_seal=True,
                verify_interval=timedelta(hours=1),
                verify_batch=500,
            )
        assert pid in report.integrity.verified
        async with engine(owner_url) as eng, eng.begin() as c:  # switched off: nothing is verified
            off = await worker_pass(c, rt=fresh_runtime, verify_interval=None)
        assert off.integrity.verified == [] and off.integrity.failed == []

    asyncio.run(run())


class Down:
    async def timestamp(self, digest: Digest) -> str:
        raise TimestampError("TSA unreachable")


class Up:
    async def timestamp(self, digest: Digest) -> str:
        return "rfc3161:dGVzdA=="


def test_a_required_timestamp_blocks_sealing_until_the_authority_answers(
    rt_url: str, owner_url: str, fresh_runtime: EvidenceRuntime
) -> None:
    tenant = uid()
    pid = Partition(tenant, "uk-1").id

    async def checkpoints() -> int:
        async with engine(owner_url) as eng, eng.begin() as c:
            return int(
                (
                    await c.execute(
                        text("SELECT count(*) FROM evidence.checkpoint WHERE partition_id = :p"),
                        {"p": pid},
                    )
                ).scalar_one()
            )

    async def run() -> None:
        await three_records(rt_url, tenant)
        async with engine(owner_url) as eng, eng.begin() as c:
            refused = await worker_pass(
                c, rt=fresh_runtime, tsa=Down(), force_seal=True, require_timestamp=True
            )
        assert refused.seal_refused == 1 and refused.checkpoints == []
        assert await checkpoints() == 0  # unsealed, and visibly so: never reported as sealed
        async with engine(owner_url) as eng, eng.begin() as c:
            relaxed = await worker_pass(
                c, rt=fresh_runtime, tsa=Down(), force_seal=True, require_timestamp=False
            )
        assert (
            relaxed.seal_refused == 0 and await checkpoints() == 1
        )  # sealed, anchored later when the authority is back
        async with engine(owner_url) as eng, eng.begin() as c:
            anchored = await worker_pass(
                c, rt=fresh_runtime, tsa=Up(), force_seal=True, require_timestamp=True
            )
        assert (
            anchored.seal_refused == 0 and anchored.anchored
        )  # the unanchored checkpoint now carries its timestamp

    asyncio.run(run())
