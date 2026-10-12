"""API idempotency over real PostgreSQL (ZID-API-001 §1.1, §4)."""

from __future__ import annotations

import asyncio
import uuid

import pytest
from conftest import ctx, engine
from zid_db import tenant_transaction
from zid_errors import ZidError
from zid_idempotency import Scope, begin, complete


def test_replay_conflict_and_tenant_isolation(rt_url: str) -> None:
    tenant, other = str(uuid.uuid4()), str(uuid.uuid4())
    scope = Scope(tenant, "client-a", "POST /v1/mandates/{id}/reservations")
    payload = {"amount": "10.00", "currency": "GBP"}

    async def run() -> None:
        async with engine(rt_url) as eng:
            async with tenant_transaction(eng, ctx(tenant)) as c:
                assert (
                    await begin(c, scope=scope, key="k-1", payload=payload) is None
                )  # first attempt proceeds
                await complete(c, scope=scope, key="k-1", status=201, body={"reservation_id": "r1"})
            async with tenant_transaction(eng, ctx(tenant)) as c:
                replay = await begin(
                    c, scope=scope, key="k-1", payload=dict(reversed(payload.items()))
                )
                assert replay is not None and (replay.status, replay.body) == (
                    201,
                    {"reservation_id": "r1"},
                )
            with pytest.raises(ZidError) as e:
                async with tenant_transaction(eng, ctx(tenant)) as c:
                    await begin(
                        c, scope=scope, key="k-1", payload={"amount": "11.00", "currency": "GBP"}
                    )
            assert e.value.code == "IDEMPOTENCY_KEY_REUSED" and e.value.http_status == 409
            async with tenant_transaction(
                eng, ctx(other)
            ) as c:  # same key, another tenant: independent
                assert (
                    await begin(
                        c,
                        scope=Scope(other, "client-a", scope.endpoint),
                        key="k-1",
                        payload=payload,
                    )
                    is None
                )
            with pytest.raises(ZidError):
                async with tenant_transaction(eng, ctx(tenant)) as c:
                    await begin(c, scope=scope, key="x" * 129, payload=payload)

    asyncio.run(run())


def test_concurrent_same_key_runs_the_effect_once(rt_url: str) -> None:
    tenant = str(uuid.uuid4())
    scope = Scope(tenant, "client-b", "POST /v1/things")
    effects: list[int] = []

    async def attempt(i: int, eng: object) -> str:
        try:
            async with tenant_transaction(eng, ctx(tenant)) as c:  # type: ignore[arg-type]
                stored = await begin(c, scope=scope, key="same", payload={"x": 1})
                if stored is not None:
                    return "replayed"
                effects.append(i)
                await asyncio.sleep(0.05)
                await complete(c, scope=scope, key="same", status=200, body={"ok": True})
                return "done"
        except ZidError as e:
            return e.code

    async def run() -> list[str]:
        async with engine(rt_url, size=10) as eng:
            return await asyncio.gather(*(attempt(i, eng) for i in range(10)))

    results = asyncio.run(run())
    assert len(effects) == 1 and results.count("done") == 1
    assert set(results) <= {"done", "replayed", "IDEMPOTENCY_IN_PROGRESS"}
