"""The synthetic authorization probe catches a cell that answers wrongly, errors or is slow (SRE-07, OPS-12)."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

from zid_platform.modules.operations.application.synthetic import ProbeCase, run_probe

CASES = [
    ProbeCase("known-allow", {"action": {"name": "a.read"}}, "ALLOW"),
    ProbeCase("known-deny", {"action": {"name": "a.delete"}}, "DENY"),
]


def cell(allow: bool = True, deny: bool = False, status: int = 200) -> Any:
    async def post(req: Mapping[str, Any]) -> tuple[int, Mapping[str, Any]]:
        return status, {"decision": allow if req["action"]["name"] == "a.read" else deny}

    return post


def test_a_cell_that_answers_correctly_passes() -> None:
    r = asyncio.run(run_probe("uk-1", cell(), CASES))
    assert r.ok and not r.failures and [c.got for c in r.cases] == ["ALLOW", "DENY"]


def test_a_wrong_answer_in_either_direction_fails_and_says_which() -> None:
    lost = asyncio.run(run_probe("uk-1", cell(allow=False), CASES))
    assert (
        not lost.ok
        and lost.failures[0].name == "known-allow"
        and "expected ALLOW, got DENY" in str(lost.failures[0].problem)
    )
    leaked = asyncio.run(
        run_probe("uk-1", cell(deny=True), CASES)
    )  # a policy regression that over-allows
    assert not leaked.ok and leaked.failures[0].name == "known-deny"


def test_errors_unreachable_cells_and_slow_answers_fail() -> None:
    assert not asyncio.run(run_probe("uk-1", cell(status=503), CASES)).ok

    async def down(req: Mapping[str, Any]) -> tuple[int, Mapping[str, Any]]:
        raise ConnectionError("no route")

    unreachable = asyncio.run(run_probe("uk-1", down, CASES))
    assert not unreachable.ok and unreachable.cases[0].got == "ERROR"

    slow = asyncio.run(run_probe("uk-1", cell(), CASES, max_latency_ms=-1))
    assert not slow.ok and "limit" in str(slow.failures[0].problem)
    assert not asyncio.run(run_probe("uk-1", cell(), [])).ok  # a probe with no cases proves nothing
