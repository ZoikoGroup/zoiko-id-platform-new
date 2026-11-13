"""Public pricing page acceptance row P50 (ZID-UX-PRICE-004): audit evidence and data-rights access are retained lawfully after a plan exit.

A company that leaves is closed, then its data is deleted by a separate, recorded request. The ledger that proves what was decided is retained (the receipts, hashes and checkpoints stay
valid), a subject under a legal hold is kept and reported, and nothing about the plan or the billing state ever closes sign-in, the company's own evidence, or its data rights.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from conftest import ctx, engine
from onboarding_fixture import onboard
from sqlalchemy import text
from zid_db import tenant_transaction
from zid_platform.modules.commercial.domain import invoicing as inv
from zid_platform.modules.evidence.application import ledger
from zid_platform.modules.evidence.application.runtime import configure_runtime, development_runtime
from zid_platform.modules.onboarding.ports import deletion_pass, request_deletion


@pytest.fixture
def rt() -> Any:
    runtime = development_runtime("uk-1")
    configure_runtime(runtime)
    yield runtime
    configure_runtime(None)


def test_the_ledger_and_the_deletion_record_survive_a_completed_exit(
    rt_url: str, owner_url: str, rt: Any
) -> None:
    o = onboard(rt_url)
    tenant = o.tenant
    now = datetime.now(UTC)

    async def count(c: Any) -> int:
        return int(
            (await c.execute(text("SELECT count(*) FROM evidence.ledger_entry"))).scalar_one()
        )

    async def run() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            for i in range(3):
                await ledger.append(
                    c, tenant_id=tenant, record_type="test.record", record_id=f"exit-{uuid.uuid4().hex[:8]}-{i}",
                    record={"n": i}, evidence_class="E1", cell_id="uk-1",
                )  # fmt: skip
            before = await count(c)
        assert before >= 3
        async with engine(owner_url) as eng, eng.begin() as c:
            await c.execute(
                text(
                    "UPDATE platform.tenant SET status = 'CLOSED', closed_at = now() WHERE tenant_id = :t"
                ),
                {"t": uuid.UUID(tenant)},
            )
            await request_deletion(c, tenant, requested_by=o.owner, now=now)
            done = next(r for r in await deletion_pass(c, now) if r["tenant_id"] == tenant)
            assert done["status"] == "COMPLETE", done
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            assert await count(c) == before, "the ledger is retained when the data is deleted"
        async with engine(owner_url) as eng, eng.begin() as c:
            row = (
                await c.execute(
                    text(
                        "SELECT status, backup_erasure_due FROM platform.tenant_deletion WHERE tenant_id = :t"
                    ),
                    {"t": uuid.UUID(tenant)},
                )
            ).one()
            assert row.status == "COMPLETE" and row.backup_erasure_due > now, (
                "what is still owed after the deletion is recorded"
            )

    asyncio.run(run())


def test_no_billing_state_and_no_plan_exit_closes_evidence_access_or_data_rights() -> None:
    for state in inv.STATES:
        for hold in (False, True):
            restricted = inv.restricted(state, hold)
            assert (
                "audit_evidence_access" not in restricted
                and "data_rights_requests" not in restricted
            ), (state, hold)
            assert "sign_in" not in restricted and "data_export" not in restricted, (state, hold)
