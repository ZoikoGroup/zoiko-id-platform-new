"""An acknowledged decision's receipt survives the process that answered (ZID-DATA-001 DAT-11).

The receipt is committed with the decision, before the response goes out. If the process dies the instant after the
caller has its answer, nothing is lost: a brand-new process finds the same receipt, with the same hash, and its outbox
event is already waiting for the relay. Nothing depends on a background worker having run.
"""

from __future__ import annotations

import asyncio
from typing import Any

from conftest import engine
from fastapi.testclient import TestClient
from onboarding_fixture import AUD, ISS, onboard
from sqlalchemy import text
from zid_platform.main import create_app
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


def test_an_acknowledged_decision_survives_the_process_that_answered(
    rt_url: str, owner_url: str
) -> None:
    """DAT-11: answer a decision, discard the process, and read the receipt back from a new one."""
    o = onboard(rt_url, extra_resources=["decision_receipt:*"])
    decision_ids: list[str] = []
    with TestClient(_app(rt_url, o)) as first:
        for i in range(3):
            r = first.post(
                "/access/v1/evaluation",
                json={
                    "subject": {"type": "principal", "id": o.second},
                    "action": {"name": "resource.write"},
                    "resource": {"type": "document", "id": f"d{i}"},
                    "context": {"purpose": "payroll"},
                },
                headers=o.bearer(o.owner, client="pep-durability"),
            )
            assert r.status_code == 200, (
                r.text
            )  # the caller has been told: from here the receipt must exist
            decision_ids.append(str(r.json()["context"]["decision_id"]))
    # the first process is gone: no memory, no worker pass, nothing pending in it

    async def committed() -> tuple[int, int]:
        async with engine(owner_url) as eng, eng.connect() as c:
            receipts = (
                await c.execute(
                    text(
                        "SELECT count(*) FROM evidence.decision_receipt WHERE tenant_id = :t AND decision_id::text = ANY(:d)"
                    ),
                    {"t": o.tenant, "d": decision_ids},
                )
            ).scalar_one()
            events = (
                await c.execute(
                    text(
                        "SELECT count(*) FROM events.outbox_event WHERE tenant_id = :t AND published_at IS NULL"
                    ),
                    {"t": o.tenant},
                )
            ).scalar_one()
            return int(receipts), int(events)

    receipts, waiting = asyncio.run(committed())
    assert receipts == len(decision_ids), "a receipt that was acknowledged is missing"
    assert waiting >= 1, "the decision's event must already be in the outbox for the relay"

    with TestClient(_app(rt_url, o)) as second:  # a new process, started after the first one died
        listed = second.get("/v1/evidence/receipts", headers=o.bearer(o.owner)).json()["items"]
        assert {str(r["decision_id"]) for r in listed} >= set(decision_ids)
        assert all(r["receipt_hash"].startswith("sha-256:") for r in listed)
