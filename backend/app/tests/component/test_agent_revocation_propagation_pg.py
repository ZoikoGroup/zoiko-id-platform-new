"""Revocation reaches the agent channel inside the budget (ZID-GAP-001 WP-17; FR-SIG-004; contracts/baseline/slo.yaml).

The plan asks for revocation to be measured through every channel, not only for people. For an agent the enforcing point is the tool
gateway: concurrent callers keep asking it for grants for a child agent while another engine (a stand-in for another process, so
nothing is shared in memory) commits the revocation. Three triggers: the sponsor is disabled, the parent agent is suspended, and the
tool is suspended. For each caller we record commit -> first refusal and check p99 against the R3 in-cell budget, the strictest one.

The commit time is stamped before the write commits, so the lags reported are upper bounds. The gateway reads committed rows inside
each request, with no cache between, so the expected lag is one request. ZID_AGENT_PROPAGATION_READERS scales it up.
"""

from __future__ import annotations

import asyncio
import math
import os
import time
import uuid
from pathlib import Path
from typing import Any

import pytest
import yaml
from conftest import ctx, engine
from gateway_fixture import World
from sqlalchemy import text
from zid_db import tenant_transaction
from zid_platform.modules.machine.application import tool_gateway as gateway
from zid_platform.modules.principal.domain.principal import PrincipalStatus
from zid_platform.modules.principal.infrastructure.principal_store import set_status

SLO = Path(__file__).resolve().parents[4] / "contracts" / "baseline" / "slo.yaml"
READERS = int(os.environ.get("ZID_AGENT_PROPAGATION_READERS", "6"))
BUDGET_S = float(
    yaml.safe_load(SLO.read_text(encoding="utf-8"))["revocation_propagation_seconds"]["R3_in_cell"][
        "p99_le"
    ]
)
ARGS = "sha256:" + "c" * 64


def p99(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, math.ceil(0.99 * len(ordered)) - 1)]


async def _decide(_q: Any) -> dict[str, Any]:
    return {"decision": True, "context": {"decision_id": str(uuid.uuid4()), "valid_until": None}}


async def measure(w: World, revoke: Any) -> tuple[list[float], float]:
    """Run READERS concurrent callers; commit `revoke` from another engine; return each caller's lag and the whole time taken."""
    committed: dict[str, float] = {}
    lags: list[float] = []
    stop = asyncio.Event()

    async def ask(eng: Any) -> list[str]:
        async with tenant_transaction(eng, ctx(w.tenant)) as conn:
            result = await gateway.authorize_tool_call(
                conn,
                w.tenant,
                gateway.ToolCall(w.child, "docs.read", "agent", "any", ARGS),
                decide=_decide,
                sign=lambda claims, typ: "grant",
                issuer="https://id.zoiko.test",
                action_catalogued=lambda action: True,
            )
        return list(result.codes)

    async def reader(eng: Any) -> None:
        while not stop.is_set():
            codes = await ask(eng)
            seen = time.perf_counter()
            if codes and "t" in committed:
                lags.append(seen - committed["t"])
                return
            await asyncio.sleep(0)

    async with engine(w.rt_url, size=READERS + 2) as readers_eng, engine(w.rt_url) as writer_eng:
        assert await ask(readers_eng) == []  # before the revocation the call is allowed
        started = time.perf_counter()
        tasks = [asyncio.create_task(reader(readers_eng)) for _ in range(READERS)]
        await asyncio.sleep(0.2)  # let the readers get going
        committed["t"] = (
            time.perf_counter()
        )  # stamped BEFORE the write commits: an upper bound on the lag
        await revoke(writer_eng)
        try:
            await asyncio.wait_for(asyncio.gather(*tasks), timeout=30)
        finally:
            stop.set()
        return lags, time.perf_counter() - started


@pytest.mark.parametrize("trigger", ["sponsor_disabled", "parent_suspended", "tool_suspended"])
def test_revocation_reaches_the_agent_channel_inside_the_budget(
    rt_url: str, owner_url: str, trigger: str
) -> None:
    w = World(rt_url, owner_url)
    w.run(w._seed())

    async def revoke(eng: Any) -> None:
        if trigger == "sponsor_disabled":
            async with tenant_transaction(eng, ctx(w.tenant)) as c:
                await set_status(c, w.sponsor, PrincipalStatus.DISABLED)
        else:
            sql = (
                "UPDATE agent.agent_profile SET status = 'SUSPENDED' WHERE agent_principal_id = :a"
                if trigger == "parent_suspended"
                else "UPDATE agent.tool_capability SET status = 'SUSPENDED' WHERE tool_id = 'docs.read'"
            )
            async with tenant_transaction(eng, ctx(w.tenant)) as c:
                await c.execute(
                    text(sql), {"a": uuid.UUID(w.root)} if trigger == "parent_suspended" else {}
                )

    lags, _total = w.run(measure(w, revoke))
    assert len(lags) == READERS, "every caller must see the revocation"
    print(
        f"agent revocation propagation [{trigger}]: p99={p99(lags) * 1000:.1f} ms max={max(lags) * 1000:.1f} ms readers={READERS}"
    )
    assert p99(lags) <= BUDGET_S, (
        f"{trigger}: p99 {p99(lags):.3f}s exceeds the R3 in-cell budget of {BUDGET_S}s"
    )
