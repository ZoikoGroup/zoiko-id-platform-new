"""Event-consumer process: inbox-deduplicated Kafka consumers of the role's modules (e.g. the
relationship projector into SpiceDB). Same image as the API, separate process (DEC-D6).

    ZID_DATABASE_URL       runtime role (RLS on: each event is handled inside its own tenant)
    ZID_KAFKA_BOOTSTRAP    MSK / Redpanda
    ZID_SPICEDB_URL, ZID_SPICEDB_KEY   relationship projection target (DEC-C4)

The evidence ingest (EVIDENCE-001 §2.1, §7.1) runs here too: every event whose payload names an
evidence class (E1-E3) becomes a protected AuditRecord, effectively once per event id (records a
producer already wrote in its own transaction share the id and are skipped).

The Shared Signals transmitter (ZID-SIGNALS-001 §21) runs here too when the process is given the
issuer's signing keys (KMS-backed `Signer`, Phase F); without them it stays off rather than
minting SETs under a key receivers cannot verify.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Mapping

from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
from zid_context import RequestContext, new_id
from zid_crypto import KeyRing
from zid_db import tenant_transaction
from zid_outbox import CloudEvent, consume
from zid_outbox.kafka import run_consumer
from zid_telemetry import configure_logging

from zid_platform.keys import KeyRings
from zid_platform.modules.relationship.infrastructure.spicedb import SpiceDB, project
from zid_platform.modules.signals.application.ssf import (
    TRANSMITTER_CONSUMER,
    TRANSMITTER_TOPICS,
    transmitter_handler,
)

PROJECTOR = "relationship.projector.v1"
EVIDENCE_INGEST = "evidence.ingest.v1"
# Domains whose events may carry evidence (both lanes: state changes take the priority lane).
EVIDENCE_DOMAINS = (
    "authority",
    "mandate",
    "agent",
    "machine",
    "organization",
    "authentication",
    "session",
    "federation",
    "verification",
    "credential",
    "signals",
    "provisioning",
    "privacy",
    "operator_access",
    "tenant",
)
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


def evidence_ingest_handler(engine: AsyncEngine, cell_id: str):  # type: ignore[no-untyped-def]
    from zid_platform.modules.evidence.ports import ingest_event

    async def apply(conn: AsyncConnection, event: CloudEvent) -> None:
        await ingest_event(conn, event)

    async def handle(event: CloudEvent) -> None:
        if (event.data or {}).get("evidence_class") not in ("E1", "E2", "E3"):
            return
        ctx = RequestContext(
            new_id("req"), event.correlationid, "consumer", cell_id, tenant_id=event.tenantid
        )
        async with tenant_transaction(engine, ctx) as conn:
            await consume(conn, EVIDENCE_INGEST, event, apply)

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
    transmitter: tuple[KeyRing, str] | None = None,
) -> int:
    """`transmitter`: (issuer keyring, issuer) to also run the SSF transmitter consumer."""
    engine = create_async_engine(database_url, pool_size=4, max_overflow=0)
    try:
        consumers = [
            run_consumer(
                bootstrap,
                [f"zid.{env}.{region}.relationship.domain"],
                PROJECTOR,
                projector_handler(engine, spicedb, cell_id),
                stop_after=stop_after,
            ),
            run_consumer(
                bootstrap,
                [
                    f"zid.{env}.{region}.{d}.{lane}"
                    for d in EVIDENCE_DOMAINS
                    for lane in ("domain", "priority")
                ],
                EVIDENCE_INGEST,
                evidence_ingest_handler(engine, cell_id),
                stop_after=stop_after,
            ),
        ]
        if transmitter is not None:
            keyring, issuer = transmitter
            consumers.append(
                run_consumer(
                    bootstrap,
                    [f"zid.{env}.{region}.{d}.{lane}" for d, lane in TRANSMITTER_TOPICS],
                    TRANSMITTER_CONSUMER,
                    transmitter_handler(engine, keyring, issuer, cell_id),
                    stop_after=stop_after,
                )
            )
        return sum(await asyncio.gather(*consumers))
    finally:
        await engine.dispose()


def main() -> None:
    cell = os.environ.get("ZID_CELL_ID", "local-1")
    configure_logging("zid-consumer", cell)
    from zid_platform.modules.evidence.ports import configure_runtime, runtime_from_settings
    from zid_platform.settings import Settings

    configure_runtime(runtime_from_settings(Settings()))  # pseudonymous handles for audit records
    asyncio.run(
        run(
            database_url=os.environ["ZID_DATABASE_URL"],
            bootstrap=os.environ["ZID_KAFKA_BOOTSTRAP"],
            spicedb=SpiceDB(os.environ["ZID_SPICEDB_URL"], os.environ["ZID_SPICEDB_KEY"]),
            env=os.environ.get("ZID_ENVIRONMENT", "local"),
            region=os.environ.get("ZID_REGION", cell),
            cell_id=cell,
            transmitter=transmitter_keys(os.environ, cell),
        )
    )


def transmitter_keys(environ: Mapping[str, str], cell: str) -> tuple[KeyRing, str] | None:
    """The SET signing ring, when this process may have one: local/test development keys shared
    with the API through ZID_DEV_SIGNING_KEYS. Deployed cells get the KMS signer (Phase F); until
    then the transmitter stays off rather than signing with a key receivers cannot fetch."""
    issuer = environ.get("ZID_TOKEN_ISSUER")
    pems = json.loads(environ.get("ZID_DEV_SIGNING_KEYS") or "{}")
    if not issuer or environ.get("ZID_ENVIRONMENT", "local") not in ("local", "test"):
        return None
    if "set" not in pems:
        return None  # a fresh random key here would not match what the API publishes
    return KeyRings.development(cell, pems).set, issuer


if __name__ == "__main__":
    main()
