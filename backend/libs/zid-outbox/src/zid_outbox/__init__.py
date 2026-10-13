"""Transactional outbox, relay and idempotent inbox (ZID-EVENTS-001 §2, §5, §7-10; DEC-D2).

- `emit` writes a CloudEvents-shaped row in the caller's transaction: the domain change and its event
  commit or roll back together (no dual write). The event id is fixed here and reused on every retry.
- `relay_once` claims unpublished rows with FOR UPDATE SKIP LOCKED plus a lease, publishes them through
  a `Publisher`, and marks them published; a crash only means the same event id is published again.
- `consume` makes a consumer effectively-once: one inbox row per (consumer, event id); a duplicate is
  skipped, a version gap raises `OutOfOrder` (ordered consumers wait), and a failing handler
  quarantines the event instead of blocking the partition forever.
Delivery is at least once; business effects are effectively once (EVENTS §9 wording lock).
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection
from zid_context import current_context, uuid7

__all__ = [
    "CloudEvent",
    "OutOfOrder",
    "Publisher",
    "consume",
    "emit",
    "event_type",
    "relay_once",
]

TYPE_PATTERN = re.compile(r"^com\.zoiko\.id\.[a-z0-9_.]+\.v[0-9]+$")
MAX_PAYLOAD_BYTES = (
    262_144  # EVENTS §11: larger evidence travels by encrypted reference (claim check)
)
PRIORITY_WORDS = (
    "revoked",
    "compromis",
    "quarantin",
    "suspended",
    "paused",
    "invalidated",
    "stale",
)


def event_type(name: str, major: int = 1) -> str:
    """`mandate.revoked` -> `com.zoiko.id.mandate.revoked.v1` (EVENTS §3)."""
    return f"com.zoiko.id.{name}.v{major}"


def lane_for(name: str) -> str:
    """Security-state changes take the priority lane, never starved by bulk traffic (DEC-B1 §4)."""
    if any(w in name for w in PRIORITY_WORDS) or name.startswith("security.state."):
        return "priority"
    if name.startswith(("evidence.", "audit.")) or "receipt" in name:
        return "evidence"
    return "domain"


@dataclass(frozen=True, slots=True)
class CloudEvent:
    id: str
    type: str
    source: str
    subject: str
    time: datetime
    tenantid: str
    cellid: str
    aggregatetype: str
    aggregateid: str
    aggregateversion: int
    data: Mapping[str, Any]
    correlationid: str
    causationid: str | None = None
    classification: str = "INTERNAL"
    dataschema: str = ""
    specversion: str = "1.0"
    datacontenttype: str = "application/json"
    extensions: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = {k: getattr(self, k) for k in self.__dataclass_fields__ if k != "extensions"}
        d["time"] = self.time.isoformat().replace("+00:00", "Z")
        return {**d, **self.extensions}


async def emit(
    conn: AsyncConnection,
    *,
    name: str,
    tenant_id: str,
    aggregate_type: str,
    aggregate_id: str,
    aggregate_version: int,
    data: Mapping[str, Any],
    cell_id: str | None = None,
    occurred_at: datetime | None = None,
    classification: str = "INTERNAL",
    causation_id: str | None = None,
) -> str:
    """Write one event row inside the caller's transaction and return its id.

    Payloads carry opaque ids and revisions only, never names, emails or raw evidence.
    """
    etype = event_type(name)
    if not TYPE_PATTERN.match(etype):
        raise ValueError(f"invalid event type {etype}")
    payload = json.dumps(data, separators=(",", ":"), sort_keys=True, default=str)
    if len(payload.encode()) > MAX_PAYLOAD_BYTES:
        raise ValueError("event payload exceeds 256 KiB; use an encrypted object reference")
    ctx = current_context()
    event_id = uuid7()
    await conn.execute(
        text("""
            INSERT INTO events.outbox_event (event_id, tenant_id, cell_id, aggregate_type, aggregate_id,
                aggregate_version, event_type, schema_id, subject, lane, classification, payload, correlation_id,
                causation_id, occurred_at)
            VALUES (:id, :t, :cell, :atype, :aid, :ver, :type, :schema, :subject, :lane, :cls, CAST(:payload AS jsonb),
                    :corr, :cause, :at)
        """),
        {
            "id": event_id,
            "t": uuid.UUID(tenant_id),
            "cell": cell_id or (ctx.cell_id if ctx else "unknown"),
            "atype": aggregate_type,
            "aid": aggregate_id,
            "ver": aggregate_version,
            "type": etype,
            "schema": f"urn:zoiko:schema:{name}:v1.0",
            "subject": f"{aggregate_type}/{aggregate_id}",
            "lane": lane_for(name),
            "cls": classification,
            "payload": payload,
            "corr": ctx.correlation_id if ctx else str(event_id),
            "cause": causation_id,
            "at": occurred_at or datetime.now(UTC),
        },
    )
    return str(event_id)


class Publisher(Protocol):
    """Durable backbone producer (MSK/Kafka in cells, Redpanda locally). Must be idempotent per event id."""

    async def publish(self, topic: str, key: str, event: CloudEvent) -> None: ...


def _event(row: Any) -> CloudEvent:
    m = row._mapping
    return CloudEvent(
        id=str(m["event_id"]),
        type=m["event_type"],
        source=f"urn:zoiko:id:{m['aggregate_type']}",
        subject=m["subject"],
        time=m["occurred_at"],
        tenantid=str(m["tenant_id"]),
        cellid=m["cell_id"],
        aggregatetype=m["aggregate_type"],
        aggregateid=m["aggregate_id"],
        aggregateversion=m["aggregate_version"],
        data=m["payload"],
        correlationid=m["correlation_id"],
        causationid=m["causation_id"],
        classification=m["classification"],
        dataschema=m["schema_id"],
    )


async def relay_once(
    conn: AsyncConnection,
    publisher: Publisher,
    *,
    env: str,
    region: str,
    batch: int = 100,
    lease: timedelta = timedelta(seconds=30),
    lanes: tuple[str, ...] = ("priority", "domain", "evidence", "integration"),
    max_attempts: int = 25,
) -> int:
    """Claim, publish and mark one batch (relay role; run in its own short transaction). Returns count published.

    Priority lane rows are claimed first. A publish failure leaves the row for the next attempt after
    the lease; after `max_attempts` it is quarantined for an operator instead of retrying forever.
    """
    rows = (
        await conn.execute(
            text("""
                SELECT * FROM events.outbox_event
                WHERE published_at IS NULL AND quarantined_reason IS NULL AND lane = ANY(:lanes)
                  AND (lease_until IS NULL OR lease_until < now())
                ORDER BY (lane = 'priority') DESC, created_at
                LIMIT :n FOR UPDATE SKIP LOCKED
            """),
            {"lanes": list(lanes), "n": batch},
        )
    ).all()
    published = 0
    for row in rows:
        ev = _event(row)
        domain = ev.type.removeprefix("com.zoiko.id.").split(".")[0]
        topic = f"zid.{env}.{region}.{domain}.{row.lane}"
        try:
            await publisher.publish(topic, f"{ev.aggregatetype}:{ev.aggregateid}", ev)
        except Exception as exc:  # noqa: BLE001 - retried later, never lost
            attempts = row.attempts + 1
            await conn.execute(
                text(
                    "UPDATE events.outbox_event SET attempts = CAST(:a AS integer), lease_until = now() + :l, "
                    "quarantined_reason = CASE WHEN CAST(:a AS integer) >= CAST(:max AS integer) THEN :why ELSE NULL END "
                    "WHERE event_id = :id"
                ),
                {
                    "a": attempts,
                    "l": lease,
                    "max": max_attempts,
                    "why": f"publish failed {attempts}x: {type(exc).__name__}",
                    "id": row.event_id,
                },
            )
            continue
        await conn.execute(
            text(
                "UPDATE events.outbox_event SET published_at = now(), attempts = attempts + 1 WHERE event_id = :id"
            ),
            {"id": row.event_id},
        )
        published += 1
    return published


class OutOfOrder(RuntimeError):
    """An ordered consumer saw a future aggregate version; retry after the gap is filled."""


async def consume(
    conn: AsyncConnection,
    consumer: str,
    event: CloudEvent,
    handler: Callable[[AsyncConnection, CloudEvent], Awaitable[None]],
    *,
    ordered: bool = False,
) -> str:
    """Apply `handler` at most once per (consumer, event id). Returns PROCESSED, DUPLICATE or QUARANTINED.

    The inbox row and the handler's effect commit in the caller's transaction, so the broker offset is
    acknowledged only after a durable local effect (EVENTS §8).
    """
    if ordered:
        last = (
            await conn.execute(
                text(
                    "SELECT max(aggregate_version) FROM events.inbox_message WHERE consumer = :c "
                    "AND aggregate_type = :t AND aggregate_id = :a AND status = 'PROCESSED'"
                ),
                {"c": consumer, "t": event.aggregatetype, "a": event.aggregateid},
            )
        ).scalar_one()
        if last is not None and event.aggregateversion > last + 1:
            raise OutOfOrder(
                f"{event.aggregatetype}/{event.aggregateid}: have {last}, got {event.aggregateversion}"
            )
    inserted = await conn.execute(
        text("""
            INSERT INTO events.inbox_message (consumer, event_id, tenant_id, aggregate_type, aggregate_id,
                aggregate_version, status)
            VALUES (:c, :id, :t, :atype, :aid, :ver, 'PROCESSED') ON CONFLICT DO NOTHING
        """),
        {
            "c": consumer,
            "id": uuid.UUID(event.id),
            "t": uuid.UUID(event.tenantid),
            "atype": event.aggregatetype,
            "aid": event.aggregateid,
            "ver": event.aggregateversion,
        },
    )
    if inserted.rowcount == 0:
        return "DUPLICATE"
    try:
        async with conn.begin_nested():
            await handler(conn, event)
    except OutOfOrder:
        raise
    except Exception as exc:  # noqa: BLE001 - poison event: park it, don't loop the partition
        await conn.execute(
            text(
                "UPDATE events.inbox_message SET status = 'QUARANTINED', reason = :r WHERE consumer = :c "
                "AND event_id = :id"
            ),
            {"r": f"{type(exc).__name__}: {exc}"[:500], "c": consumer, "id": uuid.UUID(event.id)},
        )
        return "QUARANTINED"
    return "PROCESSED"
