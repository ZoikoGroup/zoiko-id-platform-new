"""Security-state propagation under load (FR-SIG-003/004/006; ZID-SIGNALS-001 §14; AT-06).

Concurrent PEP readers keep asking for the security state of a population of principals, through
the same PIP the API uses (signals `security_view`: R1 through the process cache, R3 always from
the committed row). Meanwhile another "process" - a separate engine that never touches this
process's cache - suspends every principal. For each principal and risk class we measure commit
-> first enforcing observation and check p99 against contracts/baseline/slo.yaml.

The committed time is stamped *before* the write commits, so lags are over- not under-estimated.
Sizes are small enough for CI; ZID_PROPAGATION_PRINCIPALS / _READERS / _SECONDS scale it up for a
soak on a performance database (tests/load/propagation/README.md).
"""

from __future__ import annotations

import asyncio
import os
import random
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

import yaml
from conftest import ctx
from sqlalchemy.ext.asyncio import create_async_engine
from zid_db import tenant_transaction
from zid_platform.modules.signals.application.state_cache import SecurityStateCache, security_view
from zid_platform.modules.signals.domain.rules import STATE_CACHE_TTL, State
from zid_platform.modules.signals.infrastructure import signal_store as store

SLO = yaml.safe_load(
    (Path(__file__).resolve().parents[4] / "contracts/baseline/slo.yaml").read_text("utf-8")
)["revocation_propagation_seconds"]
PRINCIPALS = int(os.environ.get("ZID_PROPAGATION_PRINCIPALS", "60"))
READERS = int(os.environ.get("ZID_PROPAGATION_READERS", "12"))
WRITE_SPREAD_S = float(os.environ.get("ZID_PROPAGATION_SECONDS", "2"))
BUDGETS = {1: SLO["R1"]["p99_le"], 3: SLO["R3_in_cell"]["p99_le"]}


def p99(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * 0.99))]


def test_propagation_slo_under_concurrent_load(rt_url: str) -> None:
    tenant = str(uuid.uuid4())
    principals = [str(uuid.uuid4()) for _ in range(PRINCIPALS)]
    cache = SecurityStateCache()  # this process's PEP cache; the writer never invalidates it
    committed: dict[str, float] = {}
    observed: dict[tuple[str, int], float] = {}
    reads = {1: 0, 3: 0}
    done = asyncio.Event

    async def run() -> None:
        readers_engine = create_async_engine(rt_url, pool_size=READERS, max_overflow=0)
        writer_engine = create_async_engine(rt_url, pool_size=2, max_overflow=0)
        stop = done()

        async def reader(n: int) -> None:
            rng = random.Random(n)
            order = principals[:]
            while not stop.is_set():
                rng.shuffle(order)
                for p in order:
                    rc = 3 if rng.random() < 0.5 else 1
                    async with tenant_transaction(readers_engine, ctx(tenant)) as conn:
                        view = await security_view(
                            conn,
                            tenant_id=tenant,
                            principal_id=p,
                            auth_time=None,
                            risk_class=rc,
                            now=datetime.now(UTC),
                            cache=cache,
                        )
                    reads[rc] += 1
                    if view.state != "NORMAL" and (p, rc) not in observed and p in committed:
                        observed[(p, rc)] = time.monotonic()
                    if stop.is_set():
                        return

        async def writer() -> None:
            await asyncio.sleep(0.5)  # caches are warm with TRUSTED before anything changes
            gap = WRITE_SPREAD_S / len(principals)
            for p in principals:
                stamp, now = time.monotonic(), datetime.now(UTC)
                async with tenant_transaction(writer_engine, ctx(tenant)) as conn:
                    await store.write_state(
                        conn,
                        tenant_id=tenant,
                        subject_type="PRINCIPAL",
                        subject_id=p,
                        state=State.SUSPENDED,
                        previous=await store.lock_state(conn, "PRINCIPAL", p),
                        effective_at=now,
                        now=now,
                        reason="LOAD_TEST",
                        cause_signal_id=str(uuid.uuid4()),
                    )
                committed[p] = stamp
                await asyncio.sleep(gap)

        async def until_all_observed() -> None:
            deadline = time.monotonic() + WRITE_SPREAD_S + STATE_CACHE_TTL[1].total_seconds() + 10
            while time.monotonic() < deadline:
                if len(observed) == 2 * len(principals):
                    break
                await asyncio.sleep(0.05)
            stop.set()

        try:
            await asyncio.gather(
                *(reader(n) for n in range(READERS)), writer(), until_all_observed()
            )
        finally:
            await readers_engine.dispose()
            await writer_engine.dispose()

    asyncio.run(run())

    report = {}
    for rc, budget in BUDGETS.items():
        lags = [observed[(p, rc)] - committed[p] for p in principals if (p, rc) in observed]
        assert len(lags) == len(principals), f"R{rc}: {len(principals) - len(lags)} never observed"
        report[rc] = (p99(lags), max(lags))
        assert report[rc][0] <= budget, f"R{rc} p99 {report[rc][0]:.3f}s > {budget}s"
    # R3 never waits for a cache; R1 is bounded by its TTL (plus one read)
    assert report[3][1] < STATE_CACHE_TTL[1].total_seconds()
    assert report[1][1] <= STATE_CACHE_TTL[1].total_seconds() + 2
    assert cache.hits > 0 and reads[1] > 0 and reads[3] > 0
    print(
        f"\npropagation: principals={PRINCIPALS} readers={READERS} reads={reads} "
        + " ".join(
            f"R{rc}: p99={r[0] * 1000:.0f}ms max={r[1] * 1000:.0f}ms" for rc, r in report.items()
        )
    )
