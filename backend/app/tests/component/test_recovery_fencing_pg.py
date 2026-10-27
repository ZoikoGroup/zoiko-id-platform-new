"""Writer fencing, epoch advance and mandate reconciliation across cutovers, against real PostgreSQL
(ZID-RESILIENCE-001 §14-17; ZID-TENANCY-001 §15; ZID-SRE-001 §17-18).

Covers, at the platform layer (the placement epoch, the C3 writer fence and the mandate ledger):
- SRE-21, TEN-12, RES-05: fault injection and concurrent cutovers never leave two valid placement or
  writer epochs for one tenant, and never a half-moved tenant (placement and writer move together);
- TEN-15, RES-22: after a cutover only the new writer cell can spend a consumable mandate; the old
  cell and any older epoch are refused, so one budget is never decremented independently in two cells;
- RES-23: after a failback the original cell is the writer again under a NEW epoch and every older
  epoch (including the recovery cell's) is rejected;
- RES-12: open reservations at disaster time become RECONCILIATION_REQUIRED, cannot be committed or
  released by the ordinary path, and their capacity is not freed by lease expiry.

What this does not prove: a real PostgreSQL leader election, network partitions between real cells,
or the infrastructure-side fencing of the old cell's database (the CONFIRM steps of the runbook).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from conftest import ctx, engine
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from zid_db import tenant_transaction
from zid_platform.modules.mandate.domain.ledger import (
    Amounts,
    Budget,
    LedgerError,
    MandateStatus,
    ReservationStatus,
    SideEffect,
)
from zid_platform.modules.mandate.infrastructure.ledger_store import (
    MandateRecord,
    commit_reservation,
    create_mandate,
    read_balance,
    release_reservation,
    reserve_capacity,
    sweep_reservation,
)
from zid_platform.modules.mandate.ports import advance_writer, quarantine_open_reservations
from zid_platform.modules.tenant.application import residency
from zid_platform.modules.tenant.ports import advance_placement

NOW = datetime.now(UTC)
CELL = {c: uuid.uuid5(uuid.NAMESPACE_URL, f"zoiko-cell:{c}") for c in ("eu-1", "eu-1-dr")}


def uid() -> str:
    return str(uuid.uuid4())


async def seed_tenant(eng: AsyncEngine, tenant: str) -> None:
    """A tenant homed in eu-1 with eu-1-dr approved for recovery; eu-1 holds writer epoch 1."""
    async with tenant_transaction(eng, ctx(tenant)) as c:
        await residency.set_residency(
            c,
            tenant,
            profile="SRP-0",
            home_cell_id="eu-1",
            recovery_cells=["eu-1-dr"],
            prohibited_cells=[],
            operator_juris=[],
            key_profile="PLATFORM_MANAGED",
            jurisdiction_pack_id="eu-gdpr",
            approval_ref="TEST-FENCE",
            actor=uid(),
        )
        assert await advance_writer(c, tenant, CELL["eu-1"]) == 1


async def cutover(eng: AsyncEngine, tenant: str, to_cell: str, *, fault: str | None = None) -> int:
    """The ADVANCE_EPOCHS step exactly as the runbook runs it: both epochs in one transaction."""
    async with tenant_transaction(eng, ctx(tenant)) as c:
        epoch = await advance_placement(c, tenant, new_home=to_cell, actor=uid())
        if fault == "after_placement":
            raise RuntimeError("crash between the placement and the writer advance")
        writer = await advance_writer(c, tenant, CELL[to_cell])
        if fault == "before_commit":
            raise RuntimeError("crash before the cutover transaction committed")
        assert writer == epoch
        return epoch


async def state(eng: AsyncEngine, tenant: str) -> tuple[str, int, uuid.UUID, int, int]:
    """(home cell, placement epoch, writer cell, writer epoch, number of fence rows)."""
    async with tenant_transaction(eng, ctx(tenant)) as c:
        r = (
            await c.execute(
                text("SELECT home_cell_id, placement_epoch FROM platform.tenant_residency")
            )
        ).one()
        f = (await c.execute(text("SELECT cell_id, epoch FROM agent.writer_fence"))).all()
        return r.home_cell_id, int(r.placement_epoch), f[0].cell_id, int(f[0].epoch), len(f)


async def mandate(eng: AsyncEngine, tenant: str, budget: Budget, home: str = "eu-1") -> uuid.UUID:
    mid = uuid.uuid4()
    async with tenant_transaction(eng, ctx(tenant)) as c:
        await create_mandate(
            c,
            MandateRecord(
                mandate_id=mid,
                tenant_id=uuid.UUID(tenant),
                authority_grant_id=uuid.uuid4(),
                represented_principal_id=uuid.uuid4(),
                executor_principal_id=uuid.uuid4(),
                purpose_id=uuid.uuid4(),
                resource_scope_id=uuid.uuid4(),
                allowed_actions=("purchase.create",),
                budget=budget,
                home_cell_id=CELL[home],
                status=MandateStatus.ACTIVE,
                valid_from=NOW - timedelta(hours=1),
                valid_to=NOW + timedelta(days=1),
                created_by_principal_id=uuid.uuid4(),
            ),
        )
    return mid


async def reserve(
    eng: AsyncEngine, tenant: str, mid: uuid.UUID, key: str, cell: str, epoch: int
) -> str | uuid.UUID:
    """The reservation id, or the ledger's error code."""
    try:
        async with tenant_transaction(eng, ctx(tenant)) as c:
            res = await reserve_capacity(
                c,
                tenant_id=uuid.UUID(tenant),
                mandate_id=mid,
                idempotency_key=key,
                transaction_context=f"ctx-{key}",
                amounts=Amounts(Decimal(1), 1, 0),
                currency="GBP",
                cell_id=CELL[cell],
                writer_epoch=epoch,
                now=NOW,
            )
            return uuid.UUID(res.reservation_id)
    except LedgerError as e:
        return e.code


def test_cutover_is_atomic_single_writer_and_survives_fault_injection(rt_url: str) -> None:
    """SRE-21, TEN-12, RES-05."""
    tenant = uid()

    async def run() -> None:
        async with engine(rt_url) as eng:
            await seed_tenant(eng, tenant)
            start = await state(eng, tenant)
            assert start == ("eu-1", 1, CELL["eu-1"], 1, 1)

            # a crash at either point of the cutover rolls both epochs back together
            for fault in ("after_placement", "before_commit"):
                with pytest.raises(RuntimeError):
                    await cutover(eng, tenant, "eu-1-dr", fault=fault)
                assert await state(eng, tenant) == start, fault

            # concurrent, competing cutovers (e.g. two operators, or a retry racing the original):
            # every committed epoch is unique and the two counters never diverge
            targets = ["eu-1-dr", "eu-1"] * 4
            epochs = await asyncio.gather(*(cutover(eng, tenant, t) for t in targets))
            assert sorted(epochs) == list(range(2, 10)), "an epoch was issued twice or skipped"
            home, placement_epoch, writer_cell, writer_epoch, rows = await state(eng, tenant)
            assert rows == 1, "exactly one writer fence per tenant"
            assert placement_epoch == writer_epoch == 9
            assert writer_cell == CELL[home], "the writer cell is always the placement home cell"

    asyncio.run(run())


def test_only_the_current_writer_can_spend_and_old_epochs_stay_dead(rt_url: str) -> None:
    """TEN-15, RES-22, RES-23, SRE-21."""
    tenant = uid()

    async def run() -> None:
        async with engine(rt_url) as eng:
            await seed_tenant(eng, tenant)
            mid = await mandate(eng, tenant, Budget(currency="GBP", value_limit=Decimal(2)))

            # before the disaster eu-1 spends one unit under writer epoch 1
            assert isinstance(await reserve(eng, tenant, mid, "a1", "eu-1", 1), uuid.UUID)

            # failover: eu-1-dr becomes the writer under epoch 2
            assert await cutover(eng, tenant, "eu-1-dr") == 2
            for cell, epoch in (("eu-1", 1), ("eu-1", 2), ("eu-1-dr", 1)):
                code = await reserve(eng, tenant, mid, f"x-{cell}-{epoch}", cell, epoch)
                assert code == "WRITER_EPOCH_STALE", (cell, epoch)  # RES-22: no speculation
            assert isinstance(await reserve(eng, tenant, mid, "b1", "eu-1-dr", 2), uuid.UUID)
            async with tenant_transaction(eng, ctx(tenant)) as c:
                bal = await read_balance(c, mid)
            assert bal.reserved == Amounts(Decimal(2), 2, 0)  # one ledger, both spends visible
            assert await reserve(eng, tenant, mid, "b2", "eu-1-dr", 2) == "MANDATE_VALUE_LIMIT"
            async with tenant_transaction(eng, ctx(tenant)) as c:
                assert (await read_balance(c, mid)).reserved == Amounts(Decimal(2), 2, 0)

            # failback: the original cell takes over again, under a NEW epoch (3), not epoch 1
            assert await cutover(eng, tenant, "eu-1") == 3
            assert await state(eng, tenant) == ("eu-1", 3, CELL["eu-1"], 3, 1)
            for cell, epoch in (("eu-1", 1), ("eu-1", 2), ("eu-1-dr", 2), ("eu-1-dr", 3)):
                code = await reserve(eng, tenant, mid, f"y-{cell}-{epoch}", cell, epoch)
                assert code == "WRITER_EPOCH_STALE", (cell, epoch)  # RES-23: old epochs rejected
            # the state written by the recovery cell survived the failback
            async with tenant_transaction(eng, ctx(tenant)) as c:
                assert (await read_balance(c, mid)).reserved == Amounts(Decimal(2), 2, 0)

    asyncio.run(run())


def test_dr_reconciliation_blocks_uncertain_reservations(rt_url: str) -> None:
    """RES-12: an uncertain reservation stays reconciliation-blocked after DR."""
    tenant = uid()

    async def run() -> None:
        async with engine(rt_url) as eng:
            await seed_tenant(eng, tenant)
            mid = await mandate(
                eng, tenant, Budget(currency="GBP", value_limit=Decimal(10), count_limit=1)
            )
            rid = await reserve(eng, tenant, mid, "pay-1", "eu-1", 1)
            assert isinstance(rid, uuid.UUID)
            assert await cutover(eng, tenant, "eu-1-dr") == 2  # disaster failover

            async with tenant_transaction(eng, ctx(tenant)) as c:
                assert await quarantine_open_reservations(c) == 1  # the RECONCILE step
                reserved_before = (await read_balance(c, mid)).reserved
            assert reserved_before.count == 1

            common = {
                "tenant_id": uuid.UUID(tenant),
                "reservation_id": rid,
                "cell_id": CELL["eu-1-dr"],
                "writer_epoch": 2,
                "now": NOW,
            }
            for settle in (
                lambda c: commit_reservation(c, recheck_passed=True, **common),  # type: ignore[arg-type]
                lambda c: release_reservation(c, **common),  # type: ignore[arg-type]
            ):
                with pytest.raises(LedgerError) as e:
                    async with tenant_transaction(eng, ctx(tenant)) as c:
                        await settle(c)
                assert e.value.code == "RESERVATION_ALREADY_TERMINAL"

            # the lease running out with an UNKNOWN outcome never frees the capacity
            async with tenant_transaction(eng, ctx(tenant)) as c:
                res = await sweep_reservation(
                    c,
                    tenant_id=uuid.UUID(tenant),
                    reservation_id=rid,
                    cell_id=CELL["eu-1-dr"],
                    writer_epoch=2,
                    now=NOW + timedelta(days=2),
                    side_effect=SideEffect.UNKNOWN,
                    high_risk=True,
                )
                assert res.status is ReservationStatus.RECONCILIATION_REQUIRED
                assert (await read_balance(c, mid)).reserved == reserved_before
            # so a second spend cannot sneak into the held capacity
            assert await reserve(eng, tenant, mid, "pay-2", "eu-1-dr", 2) == "MANDATE_COUNT_LIMIT"

            # only the target system's answer resolves it: it did happen -> committed once
            async with tenant_transaction(eng, ctx(tenant)) as c:
                res = await sweep_reservation(
                    c,
                    tenant_id=uuid.UUID(tenant),
                    reservation_id=rid,
                    cell_id=CELL["eu-1-dr"],
                    writer_epoch=2,
                    now=NOW + timedelta(days=2),
                    side_effect=SideEffect.SUCCEEDED,
                    high_risk=True,
                )
                assert res.status is ReservationStatus.COMMITTED

    asyncio.run(run())
