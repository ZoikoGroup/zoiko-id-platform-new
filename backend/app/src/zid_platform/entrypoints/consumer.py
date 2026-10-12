"""Event-consumer process: inbox-deduplicated Kafka consumers of the role's modules (e.g. the
relationship projector into SpiceDB). Same image as the API, separate process (DEC-D6).

    ZID_DATABASE_URL       runtime role (RLS on: each event is handled inside its own tenant)
    ZID_KAFKA_BOOTSTRAP    MSK / Redpanda
    ZID_SPICEDB_URL, ZID_SPICEDB_KEY   relationship projection target (DEC-C4)
"""

from __future__ import annotations

import asyncio
import os

from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
from zid_context import RequestContext, new_id
from zid_db import tenant_transaction
from zid_outbox import CloudEvent, consume
from zid_outbox.kafka import run_consumer
from zid_telemetry import configure_logging

from zid_platform.modules.relationship.infrastructure.spicedb import SpiceDB, project

PROJECTOR = "relationship.projector.v1"
EDGE_CHANGED = "com.zoiko.id.relationship.edge.changed.v1"


def projector_handler(engine: AsyncEngine, spicedb: SpiceDB, cell_id: str):  # type: ignore[no-untyped-def]
    """Project the tenant's changed edges once per event (duplicates are skipped by the inbox)."""

    async def apply(conn: AsyncConnection, event: CloudEvent) -> None:
        await project(conn, spicedb, event.tenantid)

    async def handle(event: CloudEvent) -> None:
        if event.type != EDGE_CHANGED:
            return
        ctx = RequestContext(
            new_id("req"), event.correlationid, "consumer", cell_id, tenant_id=event.tenantid
        )
        async with tenant_transaction(engine, ctx) as conn:
            await consume(conn, PROJECTOR, event, apply)

    return handle


async def run(
    *,
    database_url: str,
    bootstrap: str,
    spicedb: SpiceDB,
    env: str,
    region: str,
    cell_id: str,
    stop_after: int | None = None,
) -> int:
    engine = create_async_engine(database_url, pool_size=4, max_overflow=0)
    try:
        return await run_consumer(
            bootstrap,
            [f"zid.{env}.{region}.relationship.domain"],
            PROJECTOR,
            projector_handler(engine, spicedb, cell_id),
            stop_after=stop_after,
        )
    finally:
        await engine.dispose()


def main() -> None:
    cell = os.environ.get("ZID_CELL_ID", "local-1")
    configure_logging("zid-consumer", cell)
    asyncio.run(
        run(
            database_url=os.environ["ZID_DATABASE_URL"],
            bootstrap=os.environ["ZID_KAFKA_BOOTSTRAP"],
            spicedb=SpiceDB(os.environ["ZID_SPICEDB_URL"], os.environ["ZID_SPICEDB_KEY"]),
            env=os.environ.get("ZID_ENVIRONMENT", "local"),
            region=os.environ.get("ZID_REGION", cell),
            cell_id=cell,
        )
    )


if __name__ == "__main__":
    main()
