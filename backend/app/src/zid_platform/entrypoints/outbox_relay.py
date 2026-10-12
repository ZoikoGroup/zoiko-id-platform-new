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
