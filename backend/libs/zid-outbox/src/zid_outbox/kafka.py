"""Kafka publisher and consumer loop for the event backbone (ZID-EVENTS-001 §6-9; DEC-D2: MSK, Redpanda locally).

Producer: idempotent (`enable_idempotence`), `acks=all`, keyed by aggregate so one aggregate's events
stay ordered in one partition; the CloudEvent id travels as the message key's companion header so
consumers dedup on it. Consumer: manual commit, offset committed only after the inbox transaction
commits (effectively-once processing, EVENTS §8).
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer

from zid_outbox import CloudEvent

__all__ = ["KafkaPublisher", "decode", "encode", "run_consumer"]


def encode(event: CloudEvent) -> bytes:
    return json.dumps(event.to_dict(), separators=(",", ":"), default=str).encode()


def decode(raw: bytes) -> CloudEvent:
    d: dict[str, Any] = json.loads(raw)
    return CloudEvent(
        id=d["id"],
        type=d["type"],
        source=d["source"],
        subject=d["subject"],
        time=datetime.fromisoformat(d["time"]),
        tenantid=d["tenantid"],
        cellid=d["cellid"],
        aggregatetype=d["aggregatetype"],
        aggregateid=d["aggregateid"],
        aggregateversion=int(d["aggregateversion"]),
        data=d["data"],
        correlationid=d["correlationid"],
        causationid=d.get("causationid"),
        classification=d.get("classification", "INTERNAL"),
        dataschema=d.get("dataschema", ""),
    )


class KafkaPublisher:
    """Implements zid_outbox.Publisher. Call `start()` before relaying and `stop()` on shutdown."""

    def __init__(self, bootstrap_servers: str, *, client_id: str = "zid-outbox-relay") -> None:
        self._producer = AIOKafkaProducer(
            bootstrap_servers=bootstrap_servers,
            client_id=client_id,
            acks="all",
            enable_idempotence=True,
            linger_ms=5,
        )

    async def start(self) -> None:
        await self._producer.start()

    async def stop(self) -> None:
        await self._producer.stop()

    async def publish(self, topic: str, key: str, event: CloudEvent) -> None:
        await self._producer.send_and_wait(
            topic,
            value=encode(event),
            key=key.encode(),
            headers=[("ce_id", event.id.encode()), ("ce_type", event.type.encode())],
        )


async def run_consumer(
    bootstrap_servers: str,
    topics: list[str],
    group_id: str,
    handle: Callable[[CloudEvent], Awaitable[None]],
    *,
    stop_after: int | None = None,
) -> int:
    """Consume until `stop_after` messages (tests) or forever. `handle` must be idempotent (use
    zid_outbox.consume inside a transaction); the offset is committed only after it returns."""
    consumer = AIOKafkaConsumer(
        *topics,
        bootstrap_servers=bootstrap_servers,
        group_id=group_id,
        enable_auto_commit=False,
        auto_offset_reset="earliest",
        isolation_level="read_committed",
    )
    await consumer.start()
    handled = 0
    try:
        async for msg in consumer:
            if msg.value is None:
                await consumer.commit()
                continue
            await handle(decode(msg.value))
            await consumer.commit()
            handled += 1
            if stop_after is not None and handled >= stop_after:
                break
    finally:
        await consumer.stop()
    return handled
