"""Outbox relay process (Event Platform): FOR UPDATE SKIP LOCKED -> Kafka with an idempotent
producer (DEC-D2). Owns events.outbox_event / events.inbox_message. Same image, separate process.

    ZID_RELAY_DATABASE_URL  connection as the relay role (BYPASSRLS: it reads every tenant's outbox)
    ZID_KAFKA_BOOTSTRAP     MSK / Redpanda bootstrap servers
    ZID_ENVIRONMENT, ZID_REGION   topic naming: zid.<env>.<region>.<domain>.<lane>
"""

from __future__ import annotations

import asyncio
import os

from sqlalchemy.ext.asyncio import create_async_engine
from zid_outbox import relay_once
from zid_outbox.kafka import KafkaPublisher
from zid_telemetry import configure_logging, get_logger

log = get_logger("zid.outbox_relay")
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection
from zid_telemetry.metrics import REGISTRY

LANE_AGE = REGISTRY.gauge(
    "zid_outbox_oldest_unpublished_seconds",
    "Age of the oldest unpublished, unquarantined outbox event per lane",
    ("lane",),
)
LANES = ("priority", "domain", "evidence", "integration")


async def record_lane_age(conn: AsyncConnection) -> None:
    """The relay's own backlog view (R3 budget: the priority lane under 2 s)."""
    rows = (
        await conn.execute(
            text(
                "SELECT lane, extract(epoch FROM now() - min(created_at)) AS age "
                "FROM events.outbox_event WHERE published_at IS NULL AND quarantined_reason IS NULL "
                "GROUP BY lane"
            )
        )
    ).all()
    ages = {r.lane: float(r.age or 0.0) for r in rows}
    for lane in LANES:
        LANE_AGE.set(ages.get(lane, 0.0), lane=lane)


async def run(
    *,
    database_url: str,
    bootstrap: str,
    env: str,
    region: str,
    idle_s: float = 0.2,
    max_batches: int | None = None,
) -> int:
    """Relay until stopped (or `max_batches`, for tests). Each batch is its own short transaction."""
    engine = create_async_engine(database_url, pool_size=2, max_overflow=0)
    publisher = KafkaPublisher(bootstrap)
    await publisher.start()
    total, batches = 0, 0
    try:
        while max_batches is None or batches < max_batches:
            async with engine.begin() as conn:
                n = await relay_once(conn, publisher, env=env, region=region)
                await record_lane_age(conn)
            total += n
            batches += 1
            if n == 0:
                await asyncio.sleep(idle_s)
    finally:
        await publisher.stop()
        await engine.dispose()
    return total


def main() -> None:
    configure_logging("zid-outbox-relay", os.environ.get("ZID_CELL_ID", "local-1"))
    asyncio.run(
        run(
            database_url=os.environ["ZID_RELAY_DATABASE_URL"],
            bootstrap=os.environ["ZID_KAFKA_BOOTSTRAP"],
            env=os.environ.get("ZID_ENVIRONMENT", "local"),
            region=os.environ.get("ZID_REGION", os.environ.get("ZID_CELL_ID", "local-1")),
        )
    )


if __name__ == "__main__":
    main()
