"""Background worker process: `python -m zid_platform.entrypoints.worker` (DEC-D5, DEC-D6).

Same image as the API, separate process. Each pass, in its own transaction per step:
- evidence: sequence receipts/audit records into the ledger, seal due checkpoints (E3 sooner),
  anchor them at the TSA when configured, dispose supporting evidence past retention (F-W1/F-W2);
- operator access: expire elapsed JIT grants and never-completed requests (F-W4);
- privacy: retention sweep of governed objects, rights-request deadline warnings (F-W3).
A failing step is logged and retried on the next pass; it never stops the others.

    ZID_WORKER_DATABASE_URL  the worker role: BYPASSRLS, SELECT/INSERT/UPDATE on the evidence,
                             platform and privacy schemas, no DELETE (falls back to
                             ZID_DATABASE_URL in local/test only)
    ZID_WORKER_INTERVAL_S    seconds between passes (default 10)
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
from zid_telemetry import configure_logging

from zid_platform.settings import Settings

log = logging.getLogger("zid.worker")
Step = Callable[[AsyncConnection, datetime], Awaitable[object]]


def steps(settings: Settings) -> list[tuple[str, Step]]:
    from zid_platform.modules.evidence.infrastructure.tsa import Rfc3161Client
    from zid_platform.modules.evidence.ports import worker_pass
    from zid_platform.modules.operator_access.ports import expire_due
    from zid_platform.modules.privacy.ports import privacy_pass

    tsa = Rfc3161Client(settings.evidence_tsa_url) if settings.evidence_tsa_url else None

    async def evidence(conn: AsyncConnection, now: datetime) -> object:
        return await worker_pass(conn, now=now, tsa=tsa)

    async def operator(conn: AsyncConnection, now: datetime) -> object:
        return await expire_due(conn, now)

    async def privacy(conn: AsyncConnection, now: datetime) -> object:
        return await privacy_pass(conn, now=now)

    return [("evidence", evidence), ("operator_access", operator), ("privacy", privacy)]


async def run_pass(engine: AsyncEngine, settings: Settings, now: datetime | None = None) -> dict[str, str]:
    """One pass over every step; returns step -> "ok" | error code (for logs and tests)."""
    now = now or datetime.now(UTC)
    out: dict[str, str] = {}
    for name, step in steps(settings):
        try:
            async with engine.begin() as conn:
                await step(conn, now)
            out[name] = "ok"
        except Exception as e:  # noqa: BLE001 - one failing step must not stop the others
            out[name] = getattr(e, "code", type(e).__name__)
            log.exception("worker step %s failed", name)
    return out


def database_url(settings: Settings) -> str:
    url = os.environ.get("ZID_WORKER_DATABASE_URL")
    if url:
        return url
    if settings.environment not in ("local", "test") or not settings.database_url:
        raise RuntimeError("ZID_WORKER_DATABASE_URL (the BYPASSRLS worker role) is required")
    return settings.database_url


async def main_loop(settings: Settings, *, passes: int | None = None) -> None:
    from zid_platform.modules.decision.ports import register_evidence_replayer
    from zid_platform.modules.evidence.ports import configure_runtime, runtime_from_settings
    from zid_platform.protected import catalog

    configure_runtime(runtime_from_settings(settings))
    register_evidence_replayer(catalog())
    engine = create_async_engine(database_url(settings), pool_size=2, max_overflow=0)
    interval = float(os.environ.get("ZID_WORKER_INTERVAL_S", "10"))
    try:
        n = 0
        while passes is None or n < passes:
            await run_pass(engine, settings)
            n += 1
            if passes is None or n < passes:
                await asyncio.sleep(interval)
    finally:
        await engine.dispose()


def main() -> None:
    settings = Settings()
    configure_logging("zid-worker", settings.cell_id)
    asyncio.run(main_loop(settings))


if __name__ == "__main__":
    main()
