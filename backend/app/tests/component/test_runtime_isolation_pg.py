"""The decision path does not depend on anything that can be lost without losing authority (SVC-14, RES-19, RES-20).

Metering is recorded beside a decision but is not part of it: if recording fails, the answer and its receipt are
unchanged. A complete cache flush changes latency only: the same request gets the same answer. Search is a
read-only query over operational records and is never consulted by a decision.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from conftest import engine
from fastapi.testclient import TestClient
from onboarding_fixture import AUD, ISS, onboard
from sqlalchemy import text
from zid_platform.main import create_app
from zid_platform.modules.decision.api import authzen
from zid_platform.modules.decision.application import access
from zid_platform.modules.signals.ports import CACHE
from zid_platform.modules.tenant.ports import forget
from zid_platform.settings import Settings


def _app(rt_url: str, o: Any) -> Any:
    return create_app(
        Settings(
            database_url=rt_url,
            token_issuer=ISS,
            token_audience=AUD,
            public_base_url=AUD,
            environment="test",
            cell_id="uk-1",
        ),
        keyring=o.ring,
    )


def _ask(api: TestClient, o: Any, n: str) -> Any:
    return api.post(
        "/access/v1/evaluation",
        json={
            "subject": {"type": "principal", "id": o.second},
            "action": {"name": "resource.write"},
            "resource": {"type": "document", "id": n},
            "context": {"purpose": "payroll"},
        },
        headers=o.bearer(o.owner, client="pep-isolation"),
    )


def test_a_metering_failure_never_changes_or_blocks_a_decision(
    rt_url: str, owner_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    o = onboard(rt_url, extra_resources=["decision_receipt:*"])

    async def broken(*a: Any, **k: Any) -> None:
        raise RuntimeError("meter store unavailable")

    with TestClient(_app(rt_url, o)) as api:
        healthy = _ask(api, o, "d0")
        monkeypatch.setattr(access, "record_billable", broken)
        degraded = _ask(api, o, "d1")
    assert healthy.status_code == degraded.status_code == 200, degraded.text
    assert healthy.json()["decision"] == degraded.json()["decision"]
    assert healthy.json()["context"]["outcome"] == degraded.json()["context"]["outcome"]

    async def receipts() -> int:
        async with engine(owner_url) as eng, eng.connect() as c:
            return int(
                (
                    await c.execute(
                        text(
                            "SELECT count(*) FROM evidence.decision_receipt WHERE tenant_id = :t AND decision_id::text = :d"
                        ),
                        {"t": o.tenant, "d": degraded.json()["context"]["decision_id"]},
                    )
                ).scalar_one()
            )

    assert asyncio.run(receipts()) == 1  # the degraded decision still has its receipt


def test_a_complete_cache_flush_changes_latency_only(rt_url: str) -> None:
    o = onboard(rt_url, extra_resources=["decision_receipt:*"])
    with TestClient(_app(rt_url, o)) as api:
        before = [_ask(api, o, f"d{i}") for i in range(3)]
        CACHE.clear()  # security-state cache
        forget(o.tenant)  # tenant routing and placement cache
        after = [_ask(api, o, f"d{i}") for i in range(3)]
    assert [r.status_code for r in before] == [r.status_code for r in after] == [200, 200, 200]
    assert [r.json()["decision"] for r in before] == [r.json()["decision"] for r in after]
    assert [r.json()["context"]["outcome"] for r in before] == [
        r.json()["context"]["outcome"] for r in after
    ]


def test_a_decision_never_consults_search() -> None:
    """RES-20: no module on the decision path imports search, so a search or index outage cannot reach it."""
    import ast
    from pathlib import Path

    modules = Path(authzen.__file__).resolve().parents[2]
    for name in (
        "decision",
        "policy_runtime",
        "authority",
        "mandate",
        "principal",
        "signals",
        "session",
    ):
        for py in (modules / name).rglob("*.py"):
            tree = ast.parse(py.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                mod = node.module if isinstance(node, ast.ImportFrom) else None
                assert not (mod and mod.startswith("zid_platform.modules.search")), (
                    f"{py} imports {mod}"
                )
