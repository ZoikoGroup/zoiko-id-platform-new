"""Transactional outbox, relay and inbox over real PostgreSQL (ZID-EVENTS-001 §5, §7-10; DEC-D2).

Covers: no dual write (rollback leaves no event), Phase C stores emitting their events, concurrent relays
never double-publish, priority lane first, retry then quarantine, consumer dedup, ordered gap detection,
poison quarantine, event immutability. The relay runs as the owner (it bypasses RLS in production too).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from conftest import CELL, ctx, engine
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection
from zid_db import tenant_transaction
from zid_outbox import CloudEvent, OutOfOrder, consume, emit, relay_once
from zid_platform.modules.mandate.domain.ledger import Amounts, Budget, LedgerError, MandateStatus
from zid_platform.modules.mandate.infrastructure.ledger_store import (
    MandateRecord,
    commit_reservation,
    create_mandate,
    reserve_capacity,
)
from zid_platform.modules.mandate.infrastructure.mandate_store import set_mandate_status

NOW = datetime.now(UTC)


def uid() -> str:
    return str(uuid.uuid4())


class Recorder:
    def __init__(self, fail_times: int = 0) -> None:
        self.sent: list[tuple[str, str, CloudEvent]] = []
        self.fail_times = fail_times

    async def publish(self, topic: str, key: str, event: CloudEvent) -> None:
        if self.fail_times > 0:
            self.fail_times -= 1
            raise ConnectionError("broker down")
        await asyncio.sleep(0.01)  # widen the race window for the concurrency test
        self.sent.append((topic, key, event))


async def events_for(c: AsyncConnection, aggregate_id: str) -> dict[str, list[tuple[str, int]]]:
    """Events per aggregate type, in aggregate-version order (ordering is only per aggregate)."""
    rows = (
        await c.execute(
            text(
                "SELECT aggregate_type, event_type, aggregate_version FROM events.outbox_event "
                "WHERE aggregate_id = :a ORDER BY aggregate_type, aggregate_version"
            ),
            {"a": aggregate_id},
        )
    ).all()
    out: dict[str, list[tuple[str, int]]] = {}
    for r in rows:
        out.setdefault(r.aggregate_type, []).append(
            (r.event_type.removeprefix("com.zoiko.id.").removesuffix(".v1"), r.aggregate_version)
        )
    return out


def test_events_commit_with_state_and_phase_c_flows_emit_them(rt_url: str, owner_url: str) -> None:
    tenant = uid()

    async def run() -> None:
        async with engine(owner_url) as eng, eng.begin() as c:
            await c.execute(
                text(
                    "INSERT INTO agent.writer_fence (tenant_id, cell_id, epoch) VALUES (:t, :c, 1)"
                ),
                {"t": uuid.UUID(tenant), "c": uuid.UUID(CELL)},
            )
        async with engine(rt_url) as eng:
            with pytest.raises(RuntimeError):  # no dual write: rollback takes the event with it
                async with tenant_transaction(eng, ctx(tenant)) as c:
                    await emit(
                        c,
                        name="mandate.created",
                        tenant_id=tenant,
                        aggregate_type="mandate",
                        aggregate_id="ghost",
                        aggregate_version=0,
                        data={},
                    )
                    raise RuntimeError("business failure")
            async with tenant_transaction(eng, ctx(tenant)) as c:
                assert await events_for(c, "ghost") == {}

                mid = uuid.uuid4()
                await create_mandate(
                    c,
                    MandateRecord(
                        mid,
                        uuid.UUID(tenant),
                        uuid.uuid4(),
                        uuid.uuid4(),
                        uuid.uuid4(),
                        uuid.uuid4(),
                        uuid.uuid4(),
                        ("purchase.create",),
                        Budget(currency="GBP", value_limit=Decimal(10), count_limit=1),
                        uuid.UUID(CELL),
                        MandateStatus.ACTIVE,
                        NOW - timedelta(hours=1),
                        NOW + timedelta(days=1),
                        uuid.uuid4(),
                    ),
                )
                res = await reserve_capacity(
                    c,
                    tenant_id=uuid.UUID(tenant),
                    mandate_id=mid,
                    idempotency_key="k",
                    transaction_context="ctx",
                    amounts=Amounts(Decimal(10), 1, 0),
                    currency="GBP",
                    cell_id=uuid.UUID(CELL),
                    writer_epoch=1,
                    now=NOW,
                )
                await reserve_capacity(
                    c,
                    tenant_id=uuid.UUID(tenant),
                    mandate_id=mid,
                    idempotency_key="k",
                    transaction_context="ctx",
                    amounts=Amounts(Decimal(10), 1, 0),
                    currency="GBP",
                    cell_id=uuid.UUID(CELL),
                    writer_epoch=1,
                    now=NOW,
                )  # idempotent replay: no event
                await commit_reservation(
                    c,
                    tenant_id=uuid.UUID(tenant),
                    reservation_id=uuid.UUID(res.reservation_id),
                    cell_id=uuid.UUID(CELL),
                    writer_epoch=1,
                    now=NOW,
                    recheck_passed=True,
                )
                assert await events_for(c, str(mid)) == {
                    "mandate": [("mandate.created", 0), ("mandate.exhausted", 1)],
                    "mandate_ledger": [
                        ("mandate.reservation.created", 1),
                        ("mandate.reservation.committed", 2),
                    ],
                }
                with pytest.raises(LedgerError):  # EXHAUSTED is terminal
                    await set_mandate_status(c, str(mid), MandateStatus.ACTIVE)

    asyncio.run(run())


def test_relay_publishes_once_priority_first_and_quarantines_after_retries(
    rt_url: str, owner_url: str
) -> None:
    tenant = uid()

    async def run() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            for i in range(30):
                await emit(
                    c,
                    name="mandate.reservation.created",
                    tenant_id=tenant,
                    aggregate_type="t_load",
                    aggregate_id=f"a{i}",
                    aggregate_version=1,
                    data={"i": i},
                )
            await emit(
                c,
                name="mandate.revoked",
                tenant_id=tenant,
                aggregate_type="t_load",
                aggregate_id="urgent",
                aggregate_version=1,
                data={},
            )
        # Other tests share this database: park their unpublished events so the batch of 50 reaches
        # this tenant's rows (otherwise their backlog fills the batch first).
        async with engine(owner_url) as eng, eng.begin() as c:
            await c.execute(
                text(
                    "UPDATE events.outbox_event SET published_at = now() "
                    "WHERE published_at IS NULL AND tenant_id <> :t"
                ),
                {"t": uuid.UUID(tenant)},
            )
        rec = Recorder()

        async def relay() -> int:
            async with engine(owner_url) as eng, eng.begin() as c:
                return await relay_once(c, rec, env="test", region="uk-1", batch=50)

        counts = await asyncio.gather(
            relay(), relay(), relay()
        )  # SKIP LOCKED: no row published twice
        mine = [e for _, _, e in rec.sent if e.tenantid == tenant]
        assert len(mine) == 31 and len({e.id for e in mine}) == 31 and sum(counts) >= 31
        first = next(t for t, _, e in rec.sent if e.tenantid == tenant)
        assert first.endswith(".mandate.priority")  # revocations jump the queue
        assert ("zid.test.uk-1.mandate.priority", "t_load:urgent") in {
            (t, k) for t, k, _ in rec.sent
        }

        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            await emit(
                c,
                name="mandate.paused",
                tenant_id=tenant,
                aggregate_type="t_fail",
                aggregate_id="x",
                aggregate_version=1,
                data={},
            )
        broken = Recorder(fail_times=10)
        for _ in range(3):
            async with engine(owner_url) as eng, eng.begin() as c:
                await c.execute(
                    text(
                        "UPDATE events.outbox_event SET lease_until = NULL WHERE aggregate_type = 't_fail'"
                    )
                )
                await relay_once(c, broken, env="test", region="uk-1", max_attempts=3)
        async with engine(owner_url) as eng, eng.begin() as c:
            row = (
                await c.execute(
                    text(
                        "SELECT attempts, published_at, quarantined_reason FROM events.outbox_event "
                        "WHERE aggregate_type = 't_fail'"
                    )
                )
            ).one()
        assert (
            row.attempts == 3
            and row.published_at is None
            and "publish failed 3x" in row.quarantined_reason
        )

    asyncio.run(run())


def _event(tenant: str, aggregate: str, version: int) -> CloudEvent:
    return CloudEvent(
        id=str(uuid.uuid4()),
        type="com.zoiko.id.mandate.paused.v1",
        source="urn:zoiko:id:mandate",
        subject=f"mandate/{aggregate}",
        time=NOW,
        tenantid=tenant,
        cellid="uk-1",
        aggregatetype="mandate",
        aggregateid=aggregate,
        aggregateversion=version,
        data={},
        correlationid="c",
    )


def test_inbox_dedups_detects_gaps_and_quarantines_poison(rt_url: str) -> None:
    tenant = uid()
    applied: list[int] = []

    async def handler(c: AsyncConnection, e: CloudEvent) -> None:
        applied.append(e.aggregateversion)

    async def poison(c: AsyncConnection, e: CloudEvent) -> None:
        raise ValueError("schema violation")

    async def run() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            e1, e2, e4 = _event(tenant, "m1", 1), _event(tenant, "m1", 2), _event(tenant, "m1", 4)
            assert await consume(c, "projector.v1", e1, handler, ordered=True) == "PROCESSED"
            assert (
                await consume(c, "projector.v1", e1, handler, ordered=True) == "DUPLICATE"
            )  # redelivery
            with pytest.raises(OutOfOrder):
                await consume(c, "projector.v1", e4, handler, ordered=True)
            assert await consume(c, "projector.v1", e2, handler, ordered=True) == "PROCESSED"
            assert applied == [1, 2]
            assert await consume(c, "other.v1", e1, handler) == "PROCESSED"  # dedup is per consumer
            assert (
                await consume(c, "projector.v1", _event(tenant, "m2", 1), poison) == "QUARANTINED"
            )

    asyncio.run(run())


def test_published_events_are_immutable(owner_url: str) -> None:
    tenant = uid()

    async def run() -> None:
        async with engine(owner_url) as eng:
            async with eng.begin() as c:
                await c.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": tenant})
                eid = await emit(
                    c,
                    name="mandate.revoked",
                    tenant_id=tenant,
                    aggregate_type="t_imm",
                    aggregate_id="z",
                    aggregate_version=1,
                    data={"a": 1},
                )
            with pytest.raises(DBAPIError, match="immutable"):
                async with eng.begin() as c:
                    await c.execute(
                        text("UPDATE events.outbox_event SET payload = '{}' WHERE event_id = :e"),
                        {"e": uuid.UUID(eid)},
                    )
            with pytest.raises(
                DBAPIError, match="outbox_event_aggregate_type_aggregate_id_aggregate_version_key"
            ):
                async with eng.begin() as c:  # one event per aggregate version
                    await emit(
                        c,
                        name="mandate.revoked",
                        tenant_id=tenant,
                        aggregate_type="t_imm",
                        aggregate_id="z",
                        aggregate_version=1,
                        data={"a": 2},
                    )

    asyncio.run(run())
