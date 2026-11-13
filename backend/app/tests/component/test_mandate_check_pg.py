"""Public pricing page acceptance row P15 (ZID-UX-PRICE-004 section 08): delegation mandates validate relationship, scope, expiry and revocation.

The question a decision asks of a mandate (ZID-MANDATE-001 section 9) is answered by `check_for_decision`: live, and covering this executor, this represented
principal, this action, this resource and this tool. It is read only and never spends budget.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from conftest import CELL as SHARED_CELL
from conftest import ctx, engine
from sqlalchemy import text
from zid_db import tenant_transaction
from zid_platform.modules.mandate.application.decision_check import check_for_decision
from zid_platform.modules.mandate.domain.ledger import Budget, MandateStatus
from zid_platform.modules.mandate.domain.subagent import MandateTerms
from zid_platform.modules.mandate.infrastructure.ledger_store import MandateRecord, create_mandate
from zid_platform.modules.mandate.infrastructure.mandate_store import set_mandate_status

CELL = uuid.UUID(SHARED_CELL)
TENANT = uuid.UUID("00000000-0000-7000-8000-0000000000d1")
NOW = datetime.now(UTC)


@pytest.fixture(scope="module", autouse=True)
def writer_fence(owner_url: str) -> None:
    async def seed() -> None:
        async with engine(owner_url) as eng, eng.begin() as c:
            await c.execute(
                text(
                    "INSERT INTO agent.writer_fence (tenant_id, cell_id, epoch) VALUES (:t, :cell, 1) ON CONFLICT (tenant_id) DO NOTHING"
                ),
                {"t": TENANT, "cell": CELL},
            )

    asyncio.run(seed())


async def make(
    eng, *, executor: uuid.UUID, represented: uuid.UUID, valid_to: timedelta = timedelta(days=1)
) -> str:
    mid = uuid.uuid4()
    terms = MandateTerms(
        mandate_id=str(mid), executor_principal_id=str(executor), represented_principal_id=str(represented), purpose_id=str(uuid.uuid4()),
        actions=frozenset({"purchase.create"}), resource_scope=frozenset({"invoice:*", "vendor:v-1"}), tools=frozenset({"erp"}),
        jurisdictions=frozenset({"GB"}), valid_from=NOW - timedelta(hours=2), valid_to=NOW + valid_to, delegation_allowed=False,
        max_delegation_depth=0, status=MandateStatus.ACTIVE,
    )  # fmt: skip
    async with tenant_transaction(eng, ctx(TENANT)) as c:
        await create_mandate(
            c,
            MandateRecord(
                mandate_id=mid, tenant_id=TENANT, authority_grant_id=uuid.uuid4(), represented_principal_id=represented, executor_principal_id=executor,
                purpose_id=uuid.UUID(terms.purpose_id), resource_scope_id=uuid.uuid4(), allowed_actions=("purchase.create",), budget=Budget(),
                home_cell_id=CELL, status=MandateStatus.ACTIVE, valid_from=terms.valid_from, valid_to=terms.valid_to, created_by_principal_id=uuid.uuid4(),
            ),
            terms=terms,
        )  # fmt: skip
    return str(mid)


async def ask(eng, mid: str, **over):
    args = {
        "executor_id": "",
        "represented_id": None,
        "action": "purchase.create",
        "resource": "invoice:42",
        "tool": None,
        "now": NOW,
        **over,
    }
    async with tenant_transaction(eng, ctx(TENANT)) as c:
        return await check_for_decision(c, mid, **args)


def test_a_mandate_covers_only_its_executor_its_represented_principal_its_action_resource_and_tool(
    rt_url: str,
) -> None:
    exe, rep = uuid.uuid4(), uuid.uuid4()

    async def go() -> None:
        async with engine(rt_url) as eng:
            mid = await make(eng, executor=exe, represented=rep)
            good = await ask(eng, mid, executor_id=str(exe), represented_id=str(rep), tool="erp")
            assert good is not None and good.active and good.in_scope and good.tool_allowed
            # the relationship: another executor, or another represented principal, is not covered
            assert not (
                await ask(eng, mid, executor_id=str(uuid.uuid4()), represented_id=str(rep))
            ).in_scope
            assert not (
                await ask(eng, mid, executor_id=str(exe), represented_id=str(uuid.uuid4()))
            ).in_scope
            # the scope: another action, a resource of another type, an unlisted tool
            assert not (
                await ask(
                    eng,
                    mid,
                    executor_id=str(exe),
                    represented_id=str(rep),
                    action="payment.release",
                )
            ).in_scope
            assert not (
                await ask(
                    eng, mid, executor_id=str(exe), represented_id=str(rep), resource="vendor:v-2"
                )
            ).in_scope
            assert (
                await ask(
                    eng, mid, executor_id=str(exe), represented_id=str(rep), resource="vendor:v-1"
                )
            ).in_scope
            assert not (
                await ask(eng, mid, executor_id=str(exe), represented_id=str(rep), tool="shell")
            ).tool_allowed

    asyncio.run(go())


def test_an_expired_paused_or_revoked_mandate_is_not_active_and_an_unknown_one_is_not_found(
    rt_url: str,
) -> None:
    exe, rep = uuid.uuid4(), uuid.uuid4()

    async def go() -> None:
        async with engine(rt_url) as eng:
            mid = await make(eng, executor=exe, represented=rep)
            later = NOW + timedelta(days=2)
            assert not (await ask(eng, mid, executor_id=str(exe), now=later)).active, (
                "past its validity window"
            )
            assert not (
                await ask(eng, mid, executor_id=str(exe), now=NOW - timedelta(days=1))
            ).active, "before it starts"
            async with tenant_transaction(eng, ctx(TENANT)) as c:
                await set_mandate_status(c, mid, MandateStatus.PAUSED)
            assert not (await ask(eng, mid, executor_id=str(exe))).active, "paused"
            async with tenant_transaction(eng, ctx(TENANT)) as c:
                await set_mandate_status(c, mid, MandateStatus.REVOKED)
            assert not (await ask(eng, mid, executor_id=str(exe))).active, "revoked"
            assert await ask(eng, str(uuid.uuid4()), executor_id=str(exe)) is None, (
                "no such mandate: the decision denies"
            )

    asyncio.run(go())


def test_another_tenant_cannot_see_the_mandate(rt_url: str) -> None:
    exe, rep = uuid.uuid4(), uuid.uuid4()

    async def go() -> None:
        async with engine(rt_url) as eng:
            mid = await make(eng, executor=exe, represented=rep)
            async with tenant_transaction(
                eng, ctx(uuid.UUID("00000000-0000-7000-8000-0000000000d2"))
            ) as c:
                assert (
                    await check_for_decision(
                        c,
                        mid,
                        executor_id=str(exe),
                        represented_id=str(rep),
                        action="purchase.create",
                        resource="invoice:1",
                        tool=None,
                        now=NOW,
                    )
                    is None
                )

    asyncio.run(go())
