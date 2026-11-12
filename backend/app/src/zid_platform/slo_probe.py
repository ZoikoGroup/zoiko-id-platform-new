"""A synthetic probe for the Document 02 section 14.2 release SLOs: `python -m zid_platform.slo_probe --base-url https://... [--token ...]`.

Run it from each region against a deployed environment. It sends a fixed number of requests to routes that need no provider call (the
"intake excluding provider calls" the document measures), records every status and duration, and reports availability and latency
percentiles against the targets: p95 <= 400 ms and availability >= 99.95 %. It exits 1 when a target is missed, so a scheduler or release
gate can use it directly. It reads no secrets from disk; the token comes from the command line or `ZID_PROBE_TOKEN`.

What it is not: a monthly availability figure (that needs the probe running continuously, with burn alerts, in two regions), and not a
substitute for load tests at expected traffic. It measures what it is pointed at and says so in its output.

Standard library only.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass

P95_TARGET_MS = 400.0
AVAILABILITY_TARGET = 99.95


@dataclass(frozen=True, slots=True)
class Sample:
    route: str
    status: int  # 0 means no answer at all
    ms: float


def percentile(values: Sequence[float], pct: float) -> float:
    """Nearest-rank percentile (the definition most SLO tooling uses). Empty input is 0."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, math.ceil(pct / 100 * len(ordered)))
    return ordered[rank - 1]


def served(status: int) -> bool:
    """A request counts against availability when the service failed to answer it: no answer, or a 5xx. A 4xx is the service working."""
    return 0 < status < 500


def summarise(samples: Sequence[Sample]) -> dict[str, object]:
    by_route: dict[str, list[Sample]] = {}
    for s in samples:
        by_route.setdefault(s.route, []).append(s)
    routes = {}
    for route, rows in sorted(by_route.items()):
        ok = [r for r in rows if served(r.status)]
        ms = [r.ms for r in ok]
        routes[route] = {
            "samples": len(rows),
            "served": len(ok),
            "p50_ms": round(percentile(ms, 50), 1),
            "p95_ms": round(percentile(ms, 95), 1),
            "p99_ms": round(percentile(ms, 99), 1),
            "max_ms": round(max(ms, default=0.0), 1),
        }
    served_n = sum(1 for s in samples if served(s.status))
    availability = 100.0 * served_n / len(samples) if samples else 0.0
    all_ms = [s.ms for s in samples if served(s.status)]
    p95 = percentile(all_ms, 95)
    breaches = []
    if not samples:
        breaches.append("no samples were taken")
    if p95 > P95_TARGET_MS:
        breaches.append(f"p95 {p95:.1f} ms is above {P95_TARGET_MS:.0f} ms")
    if samples and availability < AVAILABILITY_TARGET:
        breaches.append(f"availability {availability:.3f}% is below {AVAILABILITY_TARGET}%")
    return {
        "targets": {"p95_ms": P95_TARGET_MS, "availability_percent": AVAILABILITY_TARGET},
        "measured": {
            "samples": len(samples),
            "availability_percent": round(availability, 3),
            "p95_ms": round(p95, 1),
        },
        "routes": routes,
        "breaches": breaches,
        "met": not breaches,
        "note": "This is a sample from the place and time it ran, not a monthly availability figure.",
    }


def _request(
    base: str, method: str, path: str, token: str | None, body: object | None, timeout: float
) -> Sample:
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(base.rstrip("/") + path, data=data, method=method, headers=headers)
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp.read()
            status = resp.status
    except urllib.error.HTTPError as e:
        e.read()
        status = e.code
    except OSError:
        status = 0
    return Sample(f"{method} {path}", status, (time.perf_counter() - start) * 1000)


def routes_for(token: str | None) -> list[tuple[str, str, object | None]]:
    """The probed routes. Health needs no token; the intake routes are the ones that need no provider call."""
    out: list[tuple[str, str, object | None]] = [("GET", "/health/ready", None)]
    if token:
        out += [
            (
                "POST",
                "/v2/age-checks",
                {"threshold": 18, "estimate": {"estimated_age": 40, "margin": 3}},
            ),
            ("GET", "/v2/coverage?country=GB", None),
        ]
    return out


def run(
    base_url: str,
    token: str | None,
    samples: int,
    timeout: float = 5.0,
    send: Callable[..., Sample] = _request,
) -> dict[str, object]:
    taken: list[Sample] = []
    for _ in range(samples):
        for method, path, body in routes_for(token):
            taken.append(send(base_url, method, path, token, body, timeout))
    return summarise(taken)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--token", default=os.environ.get("ZID_PROBE_TOKEN"))
    ap.add_argument("--samples", type=int, default=100)
    ap.add_argument("--timeout", type=float, default=5.0)
    args = ap.parse_args(argv)
    report = run(args.base_url, args.token, args.samples, args.timeout)
    print(json.dumps(report, indent=2))
    return 0 if report["met"] else 1


if __name__ == "__main__":
    sys.exit(main())
