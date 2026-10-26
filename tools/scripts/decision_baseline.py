"""Measure the decision path against the local cell: latency percentiles and a throughput ceiling.

    backend/.venv/Scripts/python tools/scripts/decision_baseline.py <tenant_id> <principal_id> [--url URL]

Not the DEC-B3 gate (that is a 1-hour soak at 2x the Stage A peak on a PERF cell: tests/load/decision-gate).
This is the quick, honest baseline for a developer machine: serial latency per risk path, then the same
requests at rising concurrency to find where one API process saturates. Local/test cells only.
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
PATHS = {
    "R0/R1 application.access": ("application.access", "application", "app_1"),
    "R2/R3 invoice.approve": ("invoice.approve", "invoice", "inv_1"),
}


def pct(values: list[float], p: float) -> float:
    s = sorted(values)
    return s[min(len(s) - 1, int(p / 100 * len(s)))]


async def one(
    client: httpx.AsyncClient, url: str, token: str, principal: str, spec: tuple[str, str, str]
) -> tuple[int, float]:
    body = {
        "subject": {"type": "principal", "id": principal},
        "action": {"name": spec[0]},
        "resource": {"type": spec[1], "id": spec[2]},
    }
    t = time.perf_counter()
    r = await client.post(
        f"{url}/access/v1/evaluation", json=body, headers={"Authorization": f"Bearer {token}"}
    )
    return r.status_code, (time.perf_counter() - t) * 1000


async def run(url: str, token: str, principal: str) -> None:
    async with httpx.AsyncClient(timeout=60, limits=httpx.Limits(max_connections=200)) as client:
        for _ in range(10):  # warm up connections, caches and compiled policies
            for spec in PATHS.values():
                await one(client, url, token, principal, spec)
        print("serial latency (ms), 200 requests per path")
        for name, spec in PATHS.items():
            ms = [(await one(client, url, token, principal, spec))[1] for _ in range(200)]
            print(
                f"  {name:28s} p50={statistics.median(ms):6.1f}  p95={pct(ms, 95):6.1f}  p99={pct(ms, 99):6.1f}  max={max(ms):6.1f}"
            )
        print("throughput ceiling: closed loop, 8 seconds per concurrency level, R2/R3 path")
        spec = PATHS["R2/R3 invoice.approve"]
        for conc in (1, 2, 4, 8, 16, 32):
            done: list[float] = []
            errors = 0
            stop = time.perf_counter() + 8

            async def worker() -> None:
                nonlocal errors
                while time.perf_counter() < stop:
                    status, ms = await one(client, url, token, principal, spec)
                    if status >= 500 or status == 429:
                        errors += 1
                    done.append(ms)

            await asyncio.gather(*(worker() for _ in range(conc)))
            print(
                f"  concurrency {conc:3d}: {len(done) / 8:7.1f} decisions/s  p50={statistics.median(done):7.1f}ms  p95={pct(done, 95):7.1f}ms  errors={errors}"
            )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("tenant")
    ap.add_argument("principal")
    ap.add_argument("--url", default="http://localhost:8000")
    a = ap.parse_args()
    token = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "scripts" / "load_token.py"), a.tenant, a.principal],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    asyncio.run(run(a.url, token, a.principal))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
