"""Losing the message broker never loses a security or evidence event (ZID-RESILIENCE-001 RES-18).

Events are written to the outbox in the same transaction as the change they describe. A broker that has lost quorum
makes publishing fail; the events stay in the outbox, are retried later, priority first, in per-aggregate order, and
are never counted as sent until the broker accepted them. And a producer that cannot write its event does not make the
change either: both or neither.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest
from conftest import ctx, engine
from sqlalchemy import text
from zid_db import tenant_transaction
from zid_outbox import emit, relay_once


def uid() -> str:
    return str(uuid.uuid4())


class Broker:
    def __init__(self, up: bool) -> None:
        self.up = up
        self.sent: list[tuple[str, str, int]] = []

    async def publish(self, topic: str, key: str, event: Any) -> None:
        if not self.up:
            raise ConnectionError("no quorum")
        self.sent.append(
            (topic.rsplit(".", 1)[-1], event.type.split(".")[-3], event.aggregateversion)
        )


def test_events_survive_a_broker_outage_and_go_out_priority_first_in_order(
    rt_url: str, owner_url: str
) -> None:
    """RES-18: with the broker down nothing is lost or dropped; when it is back, priority events leave first and one
    aggregate's events leave in version order."""
    tenant = uid()
    broker = Broker(up=False)

    async def run() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            await emit(c, name="mandate.reservation.created", tenant_id=tenant, aggregate_type="res18", aggregate_id="a",
                       aggregate_version=1, data={"n": 1})  # fmt: skip
            await emit(c, name="mandate.reservation.committed", tenant_id=tenant, aggregate_type="res18", aggregate_id="a",
                       aggregate_version=2, data={"n": 2})  # fmt: skip
            await emit(c, name="evidence.export.created", tenant_id=tenant, aggregate_type="res18", aggregate_id="e",
                       aggregate_version=1, data={"n": 3})  # fmt: skip
            await emit(c, name="security.state.changed", tenant_id=tenant, aggregate_type="res18", aggregate_id="s",
                       aggregate_version=1, data={"current_state": "COMPROMISED"})  # fmt: skip
        async with engine(owner_url) as eng, eng.begin() as c:  # park every other test's backlog
            await c.execute(
                text(
                    "UPDATE events.outbox_event SET published_at = now() WHERE published_at IS NULL AND tenant_id <> :t"
                ),
                {"t": uuid.UUID(tenant)},
            )

        # the broker is down: nothing goes out, nothing is lost, nothing is called quarantined after one failure
        async with engine(owner_url) as eng, eng.begin() as c:
            assert await relay_once(c, broker, env="test", region="uk-1") == 0
        async with engine(owner_url) as eng, eng.connect() as c:
            row = (
                await c.execute(
                    text(
                        "SELECT count(*) AS n, count(*) FILTER (WHERE published_at IS NULL) AS waiting, "
                        "count(*) FILTER (WHERE quarantined_reason IS NOT NULL) AS parked, min(attempts) AS tried "
                        "FROM events.outbox_event WHERE tenant_id = :t"
                    ),
                    {"t": uuid.UUID(tenant)},
                )
            ).one()
            assert (row.n, row.waiting, row.parked) == (4, 4, 0) and row.tried >= 1

        # quorum returns (the retry pause has passed): everything leaves, once, priority first
        broker.up = True
        async with engine(owner_url) as eng, eng.begin() as c:
            await c.execute(
                text(
                    "UPDATE events.outbox_event SET lease_until = now() - interval '1 second' WHERE tenant_id = :t"
                ),
                {"t": uuid.UUID(tenant)},
            )
        async with engine(owner_url) as eng, eng.begin() as c:
            assert await relay_once(c, broker, env="test", region="uk-1") == 4
        assert broker.sent[0][0] == "priority", broker.sent  # the security event is first out
        a = [v for _, kind, v in broker.sent if kind == "reservation"]
        assert a == sorted(a) == [1, 2]  # one aggregate's events keep their order
        async with engine(owner_url) as eng, eng.begin() as c:
            assert (
                await relay_once(c, broker, env="test", region="uk-1") == 0
            )  # sent once, not again

    asyncio.run(run())


def test_a_change_and_its_event_are_written_together_or_not_at_all(
    rt_url: str, owner_url: str
) -> None:
    """RES-18: a producer that fails after writing its event leaves neither; the event is never stranded without its
    change, and a change is never acknowledged without its event."""
    tenant = uid()

    async def run() -> None:
        async with engine(rt_url) as eng:
            with pytest.raises(RuntimeError):
                async with tenant_transaction(eng, ctx(tenant)) as c:
                    await emit(c, name="security.state.changed", tenant_id=tenant, aggregate_type="res18", aggregate_id="x",
                               aggregate_version=1, data={"current_state": "COMPROMISED"})  # fmt: skip
                    raise RuntimeError("the change itself failed after its event was written")
        async with engine(owner_url) as eng, eng.connect() as c:
            n = (
                await c.execute(
                    text("SELECT count(*) FROM events.outbox_event WHERE tenant_id = :t"),
                    {"t": uuid.UUID(tenant)},
                )
            ).scalar_one()
            assert n == 0

    asyncio.run(run())
