"""The cost of one decision, counted (ZID-GAP-001 WP-06/WP-24; tests/load/decision-gate/RESULTS.md).

The measured baseline found one decision issuing about 60 SQL statements in 8 transactions, which is why a single process tops out near 40
decisions/s. Latency follows the statement count, so the count is the thing to hold: this test records every statement a warm
`POST /access/v1/evaluation` sends and fails if the totals grow past the budget. The budget is lowered as the hot path is tightened and never raised
without a reason written next to it.
"""

from __future__ import annotations

import os
import re
import uuid
from collections import Counter
from typing import Any

from conftest import CELL
from fastapi.testclient import TestClient
from onboarding_fixture import AUD, ISS, onboard
from sqlalchemy import event
from zid_platform.main import create_app
from zid_platform.settings import Settings

EVAL = "/access/v1/evaluation"
# Budget for one warm decision (proposed engineering values; see RESULTS.md for the measured start of 60 statements / 8 transactions).
MAX_STATEMENTS = int(os.environ.get("ZID_DECISION_MAX_STATEMENTS", "40"))
MAX_TRANSACTIONS = int(os.environ.get("ZID_DECISION_MAX_TRANSACTIONS", "3"))


def body() -> dict[str, Any]:
    return {
        "subject": {"type": "principal", "id": str(uuid.uuid4())},
        "action": {"name": "application.access"},
        "resource": {"type": "application", "id": "demo"},
    }


def shape(sql: str) -> str:
    return re.sub(r"\s+", " ", sql).strip()[:90]


def test_one_warm_decision_stays_inside_its_statement_budget(rt_url: str) -> None:
    o = onboard(rt_url)
    app = create_app(
        Settings(
            database_url=rt_url, token_issuer=ISS, token_audience=AUD, public_base_url=AUD,
            environment="test", cell_id="uk-1", cell_uuid=CELL, api_rate_limit_per_minute=0,
        ),
        keyring=o.ring,
    )  # fmt: skip
    statements: list[str] = []
    begins = 0
    with TestClient(app, base_url=ISS) as http:
        headers = o.bearer(o.owner)
        for _ in range(3):  # warm: caches, prepared statements, the first-use work
            assert http.post(EVAL, headers=headers, json=body()).status_code == 200

        sync_engine = app.state.engine.sync_engine

        @event.listens_for(sync_engine, "before_cursor_execute")
        def _count(_c: Any, _cur: Any, statement: str, *_a: Any) -> None:
            statements.append(shape(statement))

        @event.listens_for(sync_engine, "begin")
        def _begin(_c: Any) -> None:
            nonlocal begins
            begins += 1

        assert http.post(EVAL, headers=headers, json=body()).status_code == 200

    top = Counter(statements).most_common(8)
    print(f"decision cost: {len(statements)} statements in {begins} transactions")
    for sql, n in top:
        print(f"  {n:>3}x {sql}")
    assert len(statements) <= MAX_STATEMENTS, f"{len(statements)} statements > {MAX_STATEMENTS}"
    assert begins <= MAX_TRANSACTIONS, f"{begins} transactions > {MAX_TRANSACTIONS}"
