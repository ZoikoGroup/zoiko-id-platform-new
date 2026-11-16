"""Credential check usage meters (CR-070; GID-064): a completed claim check is one CREDENTIAL_CHECK usage fact, counted once, and never rated or invoiced.

The guardrail in the meter register is that nothing is counted when nothing was checked: a check refused before a source is asked (no pack, no
contract, a bad claim) and a check whose source was unavailable are not usage. A check never depends on the sink: with none wired it is recorded as before.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from datetime import UTC, datetime

import pytest
from conftest import ctx, engine
from sqlalchemy import text
from zid_db import tenant_transaction
from zid_platform.modules.commercial.ports import credential_usage_sink
from zid_platform.modules.credential.application import claim_checks as checks
from zid_platform.modules.credential.domain.claims import ClaimError
from zid_platform.modules.verification.ports import PackError, pack_registry

NOW = datetime(2026, 11, 16, 12, tzinfo=UTC)
JURIS = {"country": "US"}


def uid() -> str:
    return str(uuid.uuid4())


@pytest.fixture(autouse=True)
def _sink():
    checks.configure_usage_sink(credential_usage_sink)  # what the composition root does at startup
    yield
    checks.configure_usage_sink(None)


async def run(conn, tenant, subject, *, jurisdiction=None, source=None, claimed=None):
    return await checks.run_check(
        conn,
        tenant_id=tenant,
        subject_principal_id=subject,
        kind="ACADEMIC_AWARD",
        claimed=claimed
        or {"institution": "Uni of X", "program": "BSc Computing", "award_status": "CONFERRED"},
        jurisdiction=jurisdiction or JURIS,
        profession=None,
        packs=pack_registry(),
        source=source or checks.SimulatorSource("test"),
        now=NOW,
        environment="test",
    )


async def counts(c, tenant: str) -> dict[str, int]:
    rows = (
        await c.execute(
            text(
                "SELECT meter_id, count(*) AS n FROM commercial.billable_event "
                "WHERE tenant_id = :t AND meter_id LIKE 'CREDENTIAL_%' GROUP BY meter_id"
            ),
            {"t": uuid.UUID(tenant)},
        )
    ).all()
    return {r.meter_id: int(r.n) for r in rows}


def test_a_completed_check_is_one_usage_fact_counted_once_and_never_rated(rt_url: str) -> None:
    tenant, subject = uid(), uid()

    async def go() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            done = await run(c, tenant, subject)
            assert await counts(c, tenant) == {"CREDENTIAL_CHECK": 1}
            # the same check reported twice is one event (the key is derived from the check id)
            await credential_usage_sink(c, tenant, "CHECK", done.check_id, NOW, False)
            assert await counts(c, tenant) == {"CREDENTIAL_CHECK": 1}
            # a second, separate check is a second event
            await run(c, tenant, subject)
            assert await counts(c, tenant) == {"CREDENTIAL_CHECK": 2}
            # usage only: no SKU stands behind either meter, so nothing can be rated or invoiced
            skus = (
                await c.execute(
                    text(
                        "SELECT count(*) FROM commercial.sku "
                        "WHERE meter_id IN ('CREDENTIAL_CHECK', 'CREDENTIAL_SENSITIVE_CHECK')"
                    )
                )
            ).scalar_one()
            assert skus == 0

    asyncio.run(go())


def test_a_sensitive_check_is_its_own_meter(rt_url: str) -> None:
    tenant = uid()

    async def go() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            await credential_usage_sink(c, tenant, "SENSITIVE_CHECK", uid(), NOW, True)
            await credential_usage_sink(c, tenant, "CHECK", uid(), NOW, True)
            assert await counts(c, tenant) == {
                "CREDENTIAL_CHECK": 1,
                "CREDENTIAL_SENSITIVE_CHECK": 1,
            }

    asyncio.run(go())


def test_nothing_is_counted_when_nothing_was_checked(rt_url: str) -> None:
    tenant, subject = uid(), uid()

    class Raises:
        async def check(self, *_a, **_k):
            raise ConnectionError("the source is down")

    async def go() -> None:
        async with engine(rt_url) as eng:
            # a source that cannot be asked is UNAVAILABLE: the check is recorded, and it is not usage
            async with tenant_transaction(eng, ctx(tenant)) as c:
                with contextlib.suppress(
                    Exception
                ):  # the shape of that failure is the credential tests business; usage is ours
                    await run(c, tenant, subject, source=Raises())
                assert await counts(c, tenant) == {}
            # refused before any source is asked: no pack for the country, and a malformed claim
            async with tenant_transaction(eng, ctx(tenant)) as c:
                with pytest.raises((PackError, ClaimError)):
                    await run(c, tenant, subject, jurisdiction={"country": "ZZ"})
            async with tenant_transaction(eng, ctx(tenant)) as c:
                with pytest.raises(ClaimError):
                    await run(c, tenant, subject, claimed={"nonsense": "x"})
            async with tenant_transaction(eng, ctx(tenant)) as c:
                assert await counts(c, tenant) == {}

    asyncio.run(go())


def test_a_check_never_depends_on_the_sink(rt_url: str) -> None:
    tenant, subject = uid(), uid()
    checks.configure_usage_sink(None)

    async def go() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            done = await run(c, tenant, subject)
            assert done.check_id and await counts(c, tenant) == {}

    asyncio.run(go())
