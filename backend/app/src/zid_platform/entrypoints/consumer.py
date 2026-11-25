"""Event-consumer process: inbox-deduplicated Kafka consumers of the role's modules (e.g. the
relationship projector into SpiceDB). Same image as the API, separate process (DEC-D6).

    ZID_DATABASE_URL       runtime role (RLS on: each event is handled inside its own tenant)
    ZID_KAFKA_BOOTSTRAP    MSK / Redpanda
    ZID_KAFKA_CREATE_TOPICS  "true" creates missing topics at start (development and single-node brokers)
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
from typing import Any

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
from zid_platform.settings import Settings

PROJECTOR = "relationship.projector.v1"
WEBHOOK_ROUTER = "webhook.router.v1"
ALERTS = "notification.alerts.v1"
EMAIL = "communication.email.v1"
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


ANALYTICS_INGEST = "verify_analytics.ingest.v1"


def analytics_ingest_handler(engine: AsyncEngine, cell_id: str):  # type: ignore[no-untyped-def]
    """WF-02 section 04: the Command Center's facts, once per event id (the inbox dedupes; the fact table keys on the event id too)."""
    from zid_platform.modules.verify_analytics.ports import ingest, wanted

    async def apply(conn: AsyncConnection, event: CloudEvent) -> None:
        await ingest(conn, event)

    async def handle(event: CloudEvent) -> None:
        if not wanted(event.type) or not event.tenantid:
            return
        ctx = RequestContext(new_id("req"), event.correlationid, "consumer", cell_id, tenant_id=event.tenantid)
        async with tenant_transaction(engine, ctx) as conn:
            await consume(conn, ANALYTICS_INGEST, event, apply)

    return handle


def webhook_topics(env: str, region: str) -> list[str]:
    """Topics carrying the integration events an endpoint may subscribe to (EVENTS §17)."""
    from zid_platform.modules.webhook.ports import SUBSCRIBABLE

    domains = sorted({t.removeprefix("com.zoiko.id.").split(".")[0] for t in SUBSCRIBABLE})
    lanes = ("priority", "domain", "integration")
    return [f"zid.{env}.{region}.{d}.{lane}" for d in domains for lane in lanes]


def webhook_router_handler(engine: AsyncEngine, cell_id: str):  # type: ignore[no-untyped-def]
    """One delivery snapshot per subscribed ACTIVE endpoint, once per event (inbox-deduplicated)."""
    from zid_platform.modules.webhook.ports import SUBSCRIBABLE, route

    async def apply(conn: AsyncConnection, event: CloudEvent) -> None:
        await route(conn, event)

    async def handle(event: CloudEvent) -> None:
        if event.type not in SUBSCRIBABLE:
            return
        ctx = RequestContext(
            new_id("req"), event.correlationid, "consumer", cell_id, tenant_id=event.tenantid
        )
        async with tenant_transaction(engine, ctx) as conn:
            await consume(conn, WEBHOOK_ROUTER, event, apply)

    return handle


def alert_topics(env: str, region: str) -> list[str]:
    from zid_platform.modules.notification.ports import DOMAINS

    return [
        f"zid.{env}.{region}.{d}.{lane}"
        for d in DOMAINS
        for lane in ("priority", "domain", "integration")
    ]


def alert_handler(engine: AsyncEngine, cell_id: str):  # type: ignore[no-untyped-def]
    """Catalogued platform events become the tenant's alerts and tasks (UX §20), once per event."""
    from datetime import UTC, datetime

    from zid_platform.modules.configuration.ports import muted_alert_kinds
    from zid_platform.modules.notification.domain.alerts import event_name
    from zid_platform.modules.notification.ports import TEMPLATES, alert_from_event

    async def apply(conn: AsyncConnection, event: CloudEvent) -> None:
        muted = await muted_alert_kinds(
            conn
        )  # the tenant's choice; mandatory notices are never muted
        await alert_from_event(conn, event, now=datetime.now(UTC), muted=muted)

    async def handle(event: CloudEvent) -> None:
        if event_name(event.type) not in TEMPLATES or not event.tenantid:
            return
        ctx = RequestContext(
            new_id("req"), event.correlationid, "consumer", cell_id, tenant_id=event.tenantid
        )
        async with tenant_transaction(engine, ctx) as conn:
            await consume(conn, ALERTS, event, apply)

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
    idle_timeout_s: float | None = None,
    create_topics: bool = False,
    transmitter: tuple[KeyRing, str] | None = None,
    email: tuple[Any, Any] | None = None,
) -> int:
    """`transmitter`: (issuer keyring, issuer) to also run the SSF transmitter consumer.

    Every consumer runs concurrently; with `stop_after`, pass `idle_timeout_s` too, or a consumer
    whose topics stay empty would wait forever and the whole run with it."""
    engine = create_async_engine(database_url, pool_size=4, max_overflow=0)
    try:
        consumers = [
            run_consumer(
                bootstrap,
                [f"zid.{env}.{region}.relationship.domain"],
                PROJECTOR,
                projector_handler(engine, spicedb, cell_id),
                stop_after=stop_after,
                idle_timeout_s=idle_timeout_s,
                create_topics=create_topics,
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
                idle_timeout_s=idle_timeout_s,
                create_topics=create_topics,
            ),
        ]
        consumers.append(
            run_consumer(
                bootstrap,
                webhook_topics(env, region),
                WEBHOOK_ROUTER,
                webhook_router_handler(engine, cell_id),
                stop_after=stop_after,
                idle_timeout_s=idle_timeout_s,
                create_topics=create_topics,
            )
        )
        consumers.append(
            run_consumer(
                bootstrap,
                alert_topics(env, region),
                ALERTS,
                alert_handler(engine, cell_id),
                stop_after=stop_after,
                idle_timeout_s=idle_timeout_s,
                create_topics=create_topics,
            )
        )
        from zid_platform.modules.verify_analytics.ports import DOMAINS as ANALYTICS_DOMAINS

        consumers.append(
            run_consumer(
                bootstrap,
                [f"zid.{env}.{region}.{d}.{lane}" for d in ANALYTICS_DOMAINS for lane in ("priority", "domain", "integration")],
                ANALYTICS_INGEST,
                analytics_ingest_handler(engine, cell_id),
                stop_after=stop_after,
                idle_timeout_s=idle_timeout_s,
                create_topics=create_topics,
            )
        )
        if email is not None:
            from zid_platform.modules.communication.ports import email_handler, email_topics

            email_config, email_sender = email
            consumers.append(
                run_consumer(
                    bootstrap,
                    email_topics(env, region),
                    EMAIL,
                    email_handler(engine, cell_id, email_config, email_sender),
                    stop_after=stop_after,
                    idle_timeout_s=idle_timeout_s,
                    create_topics=create_topics,
                )
            )
        if transmitter is not None:
            keyring, issuer = transmitter
            consumers.append(
                run_consumer(
                    bootstrap,
                    [f"zid.{env}.{region}.{d}.{lane}" for d, lane in TRANSMITTER_TOPICS],
                    TRANSMITTER_CONSUMER,
                    transmitter_handler(engine, keyring, issuer, cell_id),
                    stop_after=stop_after,
                    idle_timeout_s=idle_timeout_s,
                    create_topics=create_topics,
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

    settings = Settings()
    configure_runtime(runtime_from_settings(settings))  # pseudonymous handles for audit records
    from zid_platform.modules.communication.ports import EmailConfig, build_sender

    # Email is off unless ZID_EMAIL_ENABLED is set; when on, a missing SMTP setting stops the process here.
    email_sender = build_sender(settings)
    email = (
        (EmailConfig.from_settings(settings), email_sender) if email_sender is not None else None
    )
    asyncio.run(
        run(
            database_url=os.environ["ZID_DATABASE_URL"],
            bootstrap=os.environ["ZID_KAFKA_BOOTSTRAP"],
            spicedb=SpiceDB(os.environ["ZID_SPICEDB_URL"], os.environ["ZID_SPICEDB_KEY"]),
            env=os.environ.get("ZID_ENVIRONMENT", "local"),
            region=os.environ.get("ZID_REGION", cell),
            cell_id=cell,
            create_topics=os.environ.get("ZID_KAFKA_CREATE_TOPICS", "").lower() == "true",
            transmitter=transmitter_keys(os.environ, cell, settings),
            email=email,
        )
    )


def transmitter_keys(
    environ: Mapping[str, str], cell: str, settings: Settings | None = None
) -> tuple[KeyRing, str] | None:
    """The SET signing ring, when this process may have one: local/test development keys shared
    with the API through ZID_DEV_SIGNING_KEYS, or, with ZID_KEY_PROVIDER=db (CR-060), the `set` key
    ring the API publishes, read from the same key store so receivers can verify it. A deployed cell
    on a KMS keeps the transmitter off until it is given the KMS signer, rather than signing with a
    key receivers cannot fetch: continuous revocation is then not pushed."""
    if settings is not None and settings.key_provider == "db" and settings.token_issuer:
        from zid_platform.crypto_runtime import kms_keyrings_from_settings

        return kms_keyrings_from_settings(settings).set, settings.token_issuer
    issuer = environ.get("ZID_TOKEN_ISSUER")
    pems = json.loads(environ.get("ZID_DEV_SIGNING_KEYS") or "{}")
    if not issuer or environ.get("ZID_ENVIRONMENT", "local") not in ("local", "test"):
        return None
    if "set" not in pems:
        return None  # a fresh random key here would not match what the API publishes
    return KeyRings.development(cell, pems).set, issuer


if __name__ == "__main__":
    main()
