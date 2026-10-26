"""Recording usage without slowing requests: counts gather in memory and are written in batches.

`record` is synchronous and constant-time. Every `interval_s` (or when the buffer grows) one background
flush writes the batch, one tenant transaction per tenant so row-level security applies. Usage is
telemetry, not billing evidence: a crash can lose the last few seconds of counts, which is accepted; a
failed flush puts the counts back so the next one retries.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncEngine
from zid_context import RequestContext, new_id
from zid_db import tenant_transaction
from zid_telemetry import get_logger

from zid_platform.modules.metering.domain.usage import (
    UsageBuffer,
    UsageKey,
    hour_of,
    status_class,
    surface_for,
)
from zid_platform.modules.metering.infrastructure import usage_store

log = get_logger("zid.metering")


class UsageRecorder:
    def __init__(self, engine_of, cell_id: str, *, interval_s: float = 10.0) -> None:  # type: ignore[no-untyped-def]
        self._engine_of = (
            engine_of  # () -> AsyncEngine | None, resolved late: the engine exists after startup
        )
        self._cell_id = cell_id
        self._interval = interval_s
        self._buffer = UsageBuffer()
        self._last_flush = time.monotonic()
        self._flushing: asyncio.Task[None] | None = None

    def record(
        self, tenant_id: str, client_id: str, path: str, status: int, *, now: datetime | None = None
    ) -> None:
        self._buffer.add(
            UsageKey(
                tenant_id,
                hour_of(now or datetime.now(UTC)),
                surface_for(path),
                client_id or "unknown",
                status_class(status),
            )
        )
        due = time.monotonic() - self._last_flush >= self._interval or len(self._buffer) >= 1000
        if due and (self._flushing is None or self._flushing.done()):
            self._flushing = asyncio.create_task(self._flush_in_background())

    async def _flush_in_background(self) -> None:
        await self.flush()

    async def flush(self) -> int:
        """Write everything gathered so far. Returns the number of counters written."""
        self._last_flush = time.monotonic()
        engine: AsyncEngine | None = self._engine_of()
        batch = self._buffer.drain()
        if engine is None or not batch:
            return 0
        by_tenant: dict[str, list[tuple[UsageKey, int]]] = {}
        for key, n in batch.items():
            by_tenant.setdefault(key.tenant_id, []).append((key, n))
        written = 0
        for tenant, items in by_tenant.items():
            ctx = RequestContext(
                new_id("req"), new_id("cor"), "metering", self._cell_id, tenant_id=tenant
            )
            try:
                async with tenant_transaction(engine, ctx) as conn:
                    for key, n in items:
                        await usage_store.add(conn, key, n)
                written += len(items)
            except Exception:  # noqa: BLE001 - telemetry must never break a request; counts go back for a retry
                log.warning("usage flush failed; will retry", extra={"component": "metering"})
                for key, n in items:
                    self._buffer.add(key, n)
        return written
