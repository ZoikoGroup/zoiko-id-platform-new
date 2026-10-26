"""Mandate ledger against real PostgreSQL (AT-04, MAND-08..12, MAND-21/22; DEC-C5; P0-03).

Needs a disposable PostgreSQL superuser URL in ZID_TEST_PG_URL (postgresql+asyncpg://...); skipped
otherwise. The suite creates a fresh database, bootstraps schemas, runs the mandate Alembic stream
and then works as a *runtime* role without BYPASSRLS, so row-level security is really in force.

    docker run -d --rm --name zid-ledger-test -e POSTGRES_PASSWORD=test -p 55432:5432 postgres:16-alpine
    ZID_TEST_PG_URL=postgresql+asyncpg://postgres:test@localhost:55432/postgres uv run pytest app/tests/component
"""

from __future__ import annotations

import asyncio
import random
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from conftest import CELL as SHARED_CELL
from conftest import ctx as shared_ctx
from conftest import engine
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine
from zid_context import RequestContext
from zid_db import tenant_transaction
from zid_platform.modules.mandate.domain.ledger import (
    Amounts,
    Budget,
    LedgerError,
    MandateStatus,
    ReservationStatus,
    SideEffect,
    invariant_holds,
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

CELL = uuid.UUID(SHARED_CELL)
OTHER_CELL = uuid.UUID("00000000-0000-7000-8000-0000000000c2")
TENANT = uuid.UUID("00000000-0000-7000-8000-00000000000a")
OTHER_TENANT = uuid.UUID("00000000-0000-7000-8000-00000000000b")
NOW = datetime.now(UTC)


@pytest.fixture(scope="module", autouse=True)
def writer_fences(owner_url: str) -> None:
    """This cell holds the writer epoch for both test tenants (owner bypasses RLS to seed it)."""

    async def seed() -> None:
        async with engine(owner_url) as eng, eng.begin() as c:
            await c.execute(
                text(
                    "INSERT INTO agent.writer_fence (tenant_id, cell_id, epoch) VALUES (:a, :cell, 3), (:b, :cell, 1) "
                    "ON CONFLICT (tenant_id) DO NOTHING"
                ),
                {"a": TENANT, "b": OTHER_TENANT, "cell": CELL},
            )

    asyncio.run(seed())


def ctx(tenant: uuid.UUID = TENANT) -> RequestContext:
    return shared_ctx(tenant)


async def new_mandate(eng: AsyncEngine, budget: Budget, tenant: uuid.UUID = TENANT) -> uuid.UUID:
    mid = uuid.uuid4()
    async with tenant_transaction(eng, ctx(tenant)) as c:
        await create_mandate(
            c,
            MandateRecord(
                mandate_id=mid,
                tenant_id=tenant,
                authority_grant_id=uuid.uuid4(),
                represented_principal_id=uuid.uuid4(),
                executor_principal_id=uuid.uuid4(),
                purpose_id=uuid.uuid4(),
                resource_scope_id=uuid.uuid4(),
                allowed_actions=("purchase.create",),
                budget=budget,
                home_cell_id=CELL,
                status=MandateStatus.ACTIVE,
                valid_from=NOW - timedelta(hours=1),
                valid_to=NOW + timedelta(days=1),
                created_by_principal_id=uuid.uuid4(),
            ),
        )
    return mid


async def try_reserve(
    eng: AsyncEngine, mid: uuid.UUID, key: str, amounts: Amounts, **kw: object
) -> str | None:
    """Reserve in its own transaction; returns the error code, or None on success."""
    try:
        async with tenant_transaction(eng, ctx()) as c:
            await reserve_capacity(
                c,
                tenant_id=TENANT,
                mandate_id=mid,
                idempotency_key=key,
                transaction_context=f"ctx-{key}",
                amounts=amounts,
                currency="GBP",
                cell_id=kw.get("cell", CELL),  # type: ignore[arg-type]
                writer_epoch=kw.get("epoch", 3),
                now=NOW,  # type: ignore[arg-type]
            )
            if kw.get("crash"):
                raise RuntimeError("process crashed before the transaction committed")
    except LedgerError as e:
        return e.code
    except RuntimeError:
        return "CRASHED"
    return None


async def reservations(c: AsyncConnection, mid: uuid.UUID) -> dict[str, list[uuid.UUID]]:
    rows = (
        await c.execute(
            text(
                "SELECT reservation_id, reservation_status FROM agent.mandate_reservation "
                "WHERE mandate_id = :m"
            ),
            {"m": mid},
        )
    ).all()
    out: dict[str, list[uuid.UUID]] = {}
    for r in rows:
        out.setdefault(r.reservation_status, []).append(r.reservation_id)
    return out


# ---------------------------------------------------------------- AT-04 / MAND-08


def test_ten_thousand_parallel_reservations_never_exceed_the_limit(rt_url: str) -> None:
    """10,000 concurrent attempts, 10% crashing mid-transaction, against a 5,000-unit value limit."""
    budget = Budget(currency="GBP", value_limit=Decimal("5000.00"), count_limit=7000)

    async def run() -> None:
        async with engine(rt_url, size=40) as eng:
            mid = await new_mandate(eng, budget)
            one = Amounts(Decimal("1.00"), 1, 0)
            rng = random.Random(7)
            results = await asyncio.gather(
                *(
                    try_reserve(eng, mid, f"k{i}", one, crash=rng.random() < 0.10)
                    for i in range(10_000)
                )
            )
            ok = results.count(None)
            crashed = results.count("CRASHED")
            refused = results.count("MANDATE_VALUE_LIMIT")
            assert ok + crashed + refused == 10_000, {
                r for r in results if r not in (None, "CRASHED")
            }
            async with tenant_transaction(eng, ctx()) as c:
                bal = await read_balance(c, mid)
                rows = await reservations(c, mid)
            assert ok == 5000, (
                ok,
                crashed,
                refused,
            )  # exactly the limit: no over- and no under-booking
            assert bal.reserved == Amounts(Decimal("5000.00"), 5000, 0)
            assert len(rows["RESERVED"]) == ok  # crashed transactions left nothing behind
            assert invariant_holds(budget, bal)

            # settle everything concurrently: half commit, half release, every call made twice
            ids = rows["RESERVED"]
            commit_ids, release_ids = ids[::2], ids[1::2]

            async def settle(rid: uuid.UUID, do_commit: bool) -> None:
                async with tenant_transaction(eng, ctx()) as c:
                    if do_commit:
                        await commit_reservation(
                            c,
                            tenant_id=TENANT,
                            reservation_id=rid,
                            cell_id=CELL,
                            writer_epoch=3,
                            now=NOW,
                            recheck_passed=True,
                        )
                    else:
                        await release_reservation(
                            c,
                            tenant_id=TENANT,
                            reservation_id=rid,
                            cell_id=CELL,
                            writer_epoch=3,
                            now=NOW,
                        )

            jobs = [settle(r, True) for r in commit_ids] + [settle(r, False) for r in release_ids]
            await asyncio.gather(
                *(
                    jobs
                    + [settle(r, True) for r in commit_ids]
                    + [settle(r, False) for r in release_ids]
                )
            )
            async with tenant_transaction(eng, ctx()) as c:
                bal = await read_balance(c, mid)
                rows = await reservations(c, mid)
            assert bal.reserved == Amounts()
            assert bal.committed == Amounts(Decimal(len(commit_ids)), len(commit_ids), 0)
            assert len(rows["COMMITTED"]) == len(commit_ids) and len(rows["RELEASED"]) == len(
                release_ids
            )

    asyncio.run(run())


# ---------------------------------------------------------------- idempotency (MAND-10/11)


def test_same_key_storm_creates_one_reservation(rt_url: str) -> None:
    async def run() -> None:
        async with engine(rt_url, size=20) as eng:
            mid = await new_mandate(eng, Budget(currency="GBP", value_limit=Decimal(100)))
            a = Amounts(Decimal("7.50"), 1, 0)
            results = await asyncio.gather(
                *(try_reserve(eng, mid, "same-key", a) for _ in range(100))
            )
            assert results == [None] * 100
            async with tenant_transaction(eng, ctx()) as c:
                assert (await read_balance(c, mid)).reserved == a
                assert len((await reservations(c, mid))["RESERVED"]) == 1
            assert (
                await try_reserve(eng, mid, "same-key", Amounts(Decimal(8), 1, 0))
                == "RESERVATION_CONFLICT"
            )

    asyncio.run(run())


# ---------------------------------------------------------------- writer fencing (MAND-21/22)


def test_stale_writer_epoch_and_wrong_cell_cannot_spend(rt_url: str) -> None:
    async def run() -> None:
        async with engine(rt_url) as eng:
            mid = await new_mandate(eng, Budget(currency="GBP", value_limit=Decimal(100)))
            a = Amounts(Decimal(1), 1, 0)
            assert await try_reserve(eng, mid, "old", a, epoch=2) == "WRITER_EPOCH_STALE"
            assert (
                await try_reserve(eng, mid, "elsewhere", a, cell=OTHER_CELL) == "WRITER_EPOCH_STALE"
            )
            async with tenant_transaction(eng, ctx()) as c:
                assert (await read_balance(c, mid)).reserved == Amounts()

    asyncio.run(run())


# ---------------------------------------------------------------- RLS and lifecycle


def test_row_level_security_hides_other_tenants(rt_url: str) -> None:
    async def run() -> None:
        async with engine(rt_url) as eng:
            mid = await new_mandate(eng, Budget(currency="GBP", value_limit=Decimal(10)))
            async with tenant_transaction(eng, ctx(OTHER_TENANT)) as c:
                with pytest.raises(LedgerError) as e:
                    await read_balance(c, mid)
                assert e.value.code == "MANDATE_NOT_FOUND"
                n = (await c.execute(text("SELECT count(*) FROM agent.mandate"))).scalar_one()
                assert n == 0
            with pytest.raises(Exception, match="row-level security"):
                async with tenant_transaction(eng, ctx(OTHER_TENANT)) as c:
                    await c.execute(
                        text(
                            "INSERT INTO agent.writer_fence (tenant_id, cell_id, epoch) VALUES (:t, :c, 9)"
                        ),
                        {"t": TENANT, "c": CELL},
                    )

    asyncio.run(run())


def test_commit_recheck_failure_never_consumes_and_exhaustion_is_automatic(rt_url: str) -> None:
    async def run() -> None:
        async with engine(rt_url) as eng:
            mid = await new_mandate(
                eng, Budget(currency="GBP", value_limit=Decimal(10), count_limit=1)
            )
            assert await try_reserve(eng, mid, "k", Amounts(Decimal(10), 1, 0)) is None
            async with tenant_transaction(eng, ctx()) as c:
                rid = (await reservations(c, mid))["RESERVED"][0]
            with pytest.raises(LedgerError) as e:
                async with tenant_transaction(eng, ctx()) as c:
                    await commit_reservation(
                        c,
                        tenant_id=TENANT,
                        reservation_id=rid,
                        cell_id=CELL,
                        writer_epoch=3,
                        now=NOW,
                        recheck_passed=False,
                    )
            assert e.value.code == "AUTHORITY_REVOKED_DURING_EXECUTION"
            async with tenant_transaction(eng, ctx()) as c:
                res = await commit_reservation(
                    c,
                    tenant_id=TENANT,
                    reservation_id=rid,
                    cell_id=CELL,
                    writer_epoch=3,
                    now=NOW,
                    recheck_passed=True,
                    execution_id="exe-1",
                )
                assert res.status is ReservationStatus.COMMITTED
                status = (
                    await c.execute(
                        text("SELECT status FROM agent.mandate WHERE mandate_id = :m"), {"m": mid}
                    )
                ).scalar_one()
            assert status == "EXHAUSTED"
            assert (
                await try_reserve(eng, mid, "k2", Amounts(Decimal(0), 0, 0)) == "MANDATE_NOT_ACTIVE"
            )

    asyncio.run(run())


def test_unknown_high_risk_outcome_stays_blocked_until_reconciled(rt_url: str) -> None:
    """MAND-14 against the database."""

    async def run() -> None:
        async with engine(rt_url) as eng:
            mid = await new_mandate(eng, Budget(currency="GBP", value_limit=Decimal(100)))
            assert await try_reserve(eng, mid, "k", Amounts(Decimal(60), 1, 0)) is None
            async with tenant_transaction(eng, ctx()) as c:
                rid = (await reservations(c, mid))["RESERVED"][0]
            later = NOW + timedelta(hours=1)
            async with tenant_transaction(eng, ctx()) as c:
                r = await sweep_reservation(
                    c,
                    tenant_id=TENANT,
                    reservation_id=rid,
                    cell_id=CELL,
                    writer_epoch=3,
                    now=later,
                    side_effect=SideEffect.UNKNOWN,
                    high_risk=True,
                )
                assert r.status is ReservationStatus.RECONCILIATION_REQUIRED
                assert (await read_balance(c, mid)).reserved.value == Decimal(60)
            assert (
                await try_reserve(eng, mid, "k2", Amounts(Decimal(50), 1, 0))
                == "MANDATE_VALUE_LIMIT"
            )
            async with tenant_transaction(eng, ctx()) as c:
                r = await sweep_reservation(
                    c,
                    tenant_id=TENANT,
                    reservation_id=rid,
                    cell_id=CELL,
                    writer_epoch=3,
                    now=later,
                    side_effect=SideEffect.NOT_EXECUTED,
                    high_risk=True,
                )
                assert r.status is ReservationStatus.EXPIRED
                assert (await read_balance(c, mid)).reserved == Amounts()

    asyncio.run(run())


def test_ledger_events_resolve_to_one_authority_chain_without_personal_data(rt_url: str) -> None:
    """MAND-25: the mandate, its reservation and its commit all name the same authority grant, the reservation
    and commit name the same transaction context, and none of them carries a personal-data field."""
    from zid_outbox import prohibited_content

    async def run() -> None:
        async with engine(rt_url) as eng:
            mid = await new_mandate(eng, Budget(currency="GBP", value_limit=Decimal(100)))
            assert await try_reserve(eng, mid, "chain", Amounts(Decimal(5), 1, 0)) is None
            async with tenant_transaction(eng, ctx()) as c:
                rid = (await reservations(c, mid))[ReservationStatus.RESERVED.value][0]
                await commit_reservation(
                    c, tenant_id=TENANT, reservation_id=rid, cell_id=CELL, writer_epoch=3,
                    now=NOW, recheck_passed=True,
                )  # fmt: skip
                rows = (
                    await c.execute(
                        text(
                            "SELECT event_type, payload FROM events.outbox_event "
                            "WHERE payload::text LIKE :like ORDER BY occurred_at"
                        ),
                        {"like": f"%{mid}%"},
                    )
                ).all()
            chain = [r.payload.get("data", r.payload) for r in rows]
            kinds = {r.event_type for r in rows}
            assert "com.zoiko.id.mandate.reservation.committed.v1" in kinds, kinds
            grants = {d["authority_grant_id"] for d in chain if d.get("authority_grant_id")}
            assert len(grants) == 1, chain  # mandate, reservation and commit: one authority grant
            contexts = {
                d["transaction_context_hash"] for d in chain if d.get("transaction_context_hash")
            }
            assert (
                len(contexts) == 1 and "ctx-chain" not in contexts
            )  # one transaction, stored only as a hash
            for d in chain:
                assert not prohibited_content(d), d

    asyncio.run(run())
