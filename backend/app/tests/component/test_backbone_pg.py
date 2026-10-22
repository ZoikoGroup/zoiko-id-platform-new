"""Event backbone end to end: PostgreSQL outbox -> relay -> Kafka -> consumer inbox -> SpiceDB (DEC-C4, DEC-D2).

Needs ZID_TEST_PG_URL, ZID_TEST_SPICEDB_URL and ZID_TEST_KAFKA (bootstrap servers).
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, datetime

import pytest
from conftest import CELL, ctx, engine
from sqlalchemy import text
from zid_db import tenant_transaction
from zid_outbox.kafka import KafkaPublisher
from zid_platform.entrypoints import consumer, outbox_relay
from zid_platform.modules.relationship.domain.relationship import (
    Relationship,
    RelationshipStatus,
    RelationshipType,
)
from zid_platform.modules.relationship.infrastructure.edge_store import record_edge
from zid_platform.modules.relationship.infrastructure.spicedb import SpiceDB, check_relation

KAFKA = os.environ.get("ZID_TEST_KAFKA")
SPICEDB_URL = os.environ.get("ZID_TEST_SPICEDB_URL")
pytestmark = pytest.mark.skipif(
    not (KAFKA and SPICEDB_URL), reason="set ZID_TEST_KAFKA and ZID_TEST_SPICEDB_URL"
)


def test_edge_change_flows_through_kafka_into_the_projection(rt_url: str, owner_url: str) -> None:
    tenant, alice, org = (str(uuid.uuid4()) for _ in range(3))
    env, region = f"t{uuid.uuid4().hex[:6]}", "uk-1"  # fresh topics per run
    sdb = SpiceDB(SPICEDB_URL or "", "test-key")

    async def run() -> None:
        await sdb.write_schema()
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            await record_edge(
                c,
                Relationship(
                    str(uuid.uuid4()),
                    tenant,
                    alice,
                    tenant,
                    RelationshipType.MEMBER_OF,
                    org,
                    tenant,
                    "scim",
                    2,
                    RelationshipStatus.ACTIVE,
                    datetime.now(UTC),
                    None,
                    relation_revision=0,
                ),
            )
        # Other tests share this database: park their unpublished events so this run's topics carry
        # only this tenant's edge (otherwise the consumer may stop on someone else's event first).
        async with engine(owner_url) as eng, eng.begin() as c:
            await c.execute(
                text(
                    "UPDATE events.outbox_event SET published_at = now() "
                    "WHERE published_at IS NULL AND tenant_id <> :t"
                ),
                {"t": uuid.UUID(tenant)},
            )
        published = await outbox_relay.run(
            database_url=owner_url, bootstrap=KAFKA or "", env=env, region=region, max_batches=2
        )
        assert published >= 1
        handled = await consumer.run(
            database_url=rt_url,
            bootstrap=KAFKA or "",
            spicedb=sdb,
            env=env,
            region=region,
            cell_id=CELL,
            stop_after=1,
            idle_timeout_s=10,  # the evidence consumer has nothing to read here
        )
        assert handled == 1
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            r = await check_relation(
                c,
                sdb,
                tenant_id=tenant,
                obj=org,
                relation=RelationshipType.MEMBER_OF,
                subject=alice,
                risk_level=3,
            )
            assert r.allowed, r
            processed = (
                await c.execute(
                    text("SELECT count(*) FROM events.inbox_message WHERE consumer = :c"),
                    {"c": consumer.PROJECTOR},
                )
            ).scalar_one()
            assert processed == 1

        # Redelivery (at-least-once): the same event published again is a duplicate in the inbox.
        async with engine(owner_url) as eng, eng.begin() as c:
            await c.execute(
                text("UPDATE events.outbox_event SET published_at = NULL WHERE tenant_id = :t"),
                {"t": uuid.UUID(tenant)},
            )
        await outbox_relay.run(
            database_url=owner_url, bootstrap=KAFKA or "", env=env, region=region, max_batches=2
        )
        await consumer.run(
            database_url=rt_url,
            bootstrap=KAFKA or "",
            spicedb=sdb,
            env=env,
            region=region,
            cell_id=CELL,
            stop_after=1,
            idle_timeout_s=10,  # the evidence consumer has nothing to read here
        )
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            rows = (
                await c.execute(
                    text("SELECT count(*) FROM events.inbox_message WHERE consumer = :c"),
                    {"c": consumer.PROJECTOR},
                )
            ).scalar_one()
            assert rows == 1  # one inbox row: the redelivered event was not applied twice

    asyncio.run(run())


def test_kafka_publisher_round_trip_keeps_the_cloudevent() -> None:
    from zid_outbox import CloudEvent
    from zid_outbox.kafka import decode, encode

    ev = CloudEvent(
        id=str(uuid.uuid4()),
        type="com.zoiko.id.mandate.revoked.v1",
        source="urn:zoiko:id:mandate",
        subject="mandate/m1",
        time=datetime.now(UTC),
        tenantid=str(uuid.uuid4()),
        cellid="uk-1",
        aggregatetype="mandate",
        aggregateid="m1",
        aggregateversion=3,
        data={"status": "REVOKED"},
        correlationid="c1",
    )
    assert decode(encode(ev)) == ev
    assert KafkaPublisher  # importable with the optional dependency installed
