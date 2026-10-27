"""Synthetic authorization probe (ZID-SRE-001 §SLI; SRE-07, OPS-12). Pure logic, transport injected.

A probe asks the decision endpoint of one cell a small set of questions whose answers are known: some must be
ALLOW and some must be DENY. A cell that answers wrongly in either direction, errors, or answers too slowly fails
the probe. Run it against every production cell (an outage or a policy regression shows up before a customer
reports it); the result says which case failed and how, never a bare "unhealthy".

The probe never decides what is allowed. The fixtures name their expected answer, and a fixture whose subject is
real authority is a synthetic tenant's, never a customer's.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

Post = Callable[[Mapping[str, Any]], Awaitable[tuple[int, Mapping[str, Any]]]]


@dataclass(frozen=True, slots=True)
class ProbeCase:
    name: str
    request: Mapping[str, Any]
    expect: str  # "ALLOW" | "DENY"


@dataclass(frozen=True, slots=True)
class CaseResult:
    name: str
    ok: bool
    expected: str
    got: str  # ALLOW | DENY | ERROR
    latency_ms: float
    problem: str | None


@dataclass(frozen=True, slots=True)
class ProbeResult:
    cell_id: str
    ok: bool
    cases: tuple[CaseResult, ...]

    @property
    def failures(self) -> tuple[CaseResult, ...]:
        return tuple(c for c in self.cases if not c.ok)


async def run_probe(
    cell_id: str, post: Post, cases: Sequence[ProbeCase], *, max_latency_ms: float = 500.0
) -> ProbeResult:
    out: list[CaseResult] = []
    for case in cases:
        started = time.perf_counter()
        try:
            status, body = await post(case.request)
        except Exception as e:  # noqa: BLE001 - an unreachable or broken cell is the finding, whatever raised
            ms = (time.perf_counter() - started) * 1000
            out.append(
                CaseResult(case.name, False, case.expect, "ERROR", ms, f"{type(e).__name__}")
            )
            continue
        ms = (time.perf_counter() - started) * 1000
        if status != 200 or not isinstance(body.get("decision"), bool):
            out.append(CaseResult(case.name, False, case.expect, "ERROR", ms, f"HTTP {status}"))
            continue
        got = "ALLOW" if body["decision"] else "DENY"
        problem = (
            f"expected {case.expect}, got {got}"
            if got != case.expect
            else (f"took {ms:.0f} ms (limit {max_latency_ms:.0f})" if ms > max_latency_ms else None)
        )
        out.append(CaseResult(case.name, problem is None, case.expect, got, ms, problem))
    return ProbeResult(cell_id, all(c.ok for c in out) and bool(out), tuple(out))
