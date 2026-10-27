"""A company's ledger fence is created by the cell it lives in, on first need (MAND-21; DEC-C5).

Registration creates no fence, so without this every mandate write answered WRITER_EPOCH_STALE for any company but the
operator's. The claim must be atomic, must never replace an existing fence, and must not claim a company homed elsewhere.
"""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

import pytest
from conftest import ctx, engine
from onboarding_fixture import onboard
from sqlalchemy import text
from zid_db import tenant_transaction
from zid_errors import ZidError
from zid_platform.api_auth import cell_writer_epoch

CELL_A = str(uuid.uuid4())
CELL_B = str(uuid.uuid4())


def request(cell_uuid: str, home_cell: str) -> SimpleNamespace:
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(cell_uuid=cell_uuid, home_cell=home_cell))
    )


def test_the_home_cell_claims_an_unfenced_company_once_and_only_once(
    rt_url: str, owner_url: str
) -> None:
    o = onboard(rt_url)  # a real company, homed in "uk-1", with no fence

    async def run() -> None:
        async with engine(rt_url) as eng:
            async with tenant_transaction(eng, ctx(o.tenant)) as c:
                assert await cell_writer_epoch(request(CELL_A, "uk-1"), c, o.tenant) == (
                    uuid.UUID(CELL_A),
                    1,
                )
            async with tenant_transaction(
                eng, ctx(o.tenant)
            ) as c:  # again: same fence, epoch unchanged
                assert await cell_writer_epoch(request(CELL_A, "uk-1"), c, o.tenant) == (
                    uuid.UUID(CELL_A),
                    1,
                )
            async with tenant_transaction(
                eng, ctx(o.tenant)
            ) as c:  # another cell does not take it over
                with pytest.raises(ZidError) as e:
                    await cell_writer_epoch(request(CELL_B, "uk-1"), c, o.tenant)
                assert e.value.code == "WRITER_EPOCH_STALE"
        async with engine(owner_url) as eng, eng.begin() as c:
            rows = (
                await c.execute(
                    text("SELECT cell_id, epoch FROM agent.writer_fence WHERE tenant_id = :t"),
                    {"t": uuid.UUID(o.tenant)},
                )
            ).all()
            assert [(str(r.cell_id), r.epoch) for r in rows] == [(CELL_A, 1)]

    asyncio.run(run())


def test_a_cell_that_is_not_the_companys_home_claims_nothing(rt_url: str, owner_url: str) -> None:
    o = onboard(rt_url)

    async def run() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(o.tenant)) as c:
            with pytest.raises(ZidError) as e:
                await cell_writer_epoch(request(CELL_B, "eu-1"), c, o.tenant)
            assert e.value.code == "WRITER_EPOCH_STALE"
        async with engine(owner_url) as eng, eng.begin() as c:
            n = (
                await c.execute(
                    text("SELECT count(*) FROM agent.writer_fence WHERE tenant_id = :t"),
                    {"t": uuid.UUID(o.tenant)},
                )
            ).scalar_one()
            assert n == 0

    asyncio.run(run())
