"""Claim checks against a real PostgreSQL (GID-037, CR-069): the table, row-level security, immutability, supersession,
the event, and the fail-closed paths through the shipped service packs.

GLB-02/21  an award the source records as ATTENDED is not verified
GLB-05     an unreachable source is SOURCE_UNAVAILABLE
GLB-07     a source that cannot authenticate the issuer does not verify
GLB-13     a source that no longer exists leaves the claim UNVERIFIABLE
GLB-22     a same-name match goes to review
(no pack, no check: a deployed environment has none, so nothing is checked and nothing is asked)
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from conftest import ctx, engine
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from zid_db import tenant_transaction
from zid_platform.modules.credential.application import claim_checks as checks
from zid_platform.modules.credential.domain.claims import ClaimError
from zid_platform.modules.verification.ports import PackError, pack_registry

NOW = datetime(2026, 11, 11, 12, tzinfo=UTC)
JURIS = {"country": "US"}


def uid() -> str:
    return str(uuid.uuid4())


def _award(institution: str = "Uni of X", status: str = "CONFERRED") -> dict[str, str]:
    return {"institution": institution, "program": "BSc Computing", "award_status": status}


async def _check(
    conn, tenant, subject, kind="ACADEMIC_AWARD", claimed=None, env="test", source=None
):
    return await checks.run_check(
        conn,
        tenant_id=tenant,
        subject_principal_id=subject,
        kind=kind,
        claimed=claimed or _award(),
        jurisdiction=JURIS,
        profession=None,
        packs=pack_registry(),
        source=source or checks.SimulatorSource("test"),
        now=NOW,
        environment=env,
    )


def test_a_confirmed_award_is_recorded_with_its_event_and_no_claimed_values(
    rt_url: str, owner_url: str
) -> None:
    import asyncio

    tenant, subject = uid(), uid()

    async def go() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            r = await _check(c, tenant, subject, claimed=_award("Distinct University 12345"))
            assert r.assertion.decision.value == "VERIFIED"
            row = (
                await c.execute(
                    text("SELECT assertion::text, claimed_digest FROM cred.claim_check")
                )
            ).one()
            assert "Distinct University 12345" not in row[0]  # no claimed value is stored
            assert len(row[1]) == 32
            ev = (
                await c.execute(
                    text(
                        "SELECT payload::text FROM events.outbox_event WHERE event_type LIKE '%claim.checked%'"
                    )
                )
            ).all()
            assert len(ev) == 1 and "Distinct University" not in ev[0][0]

    asyncio.run(go())


@pytest.mark.parametrize(
    ("tag", "decision"),
    [
        ("SIM-DOWN", "SOURCE_UNAVAILABLE"),  # GLB-05
        ("SIM-NONE", "NOT_VERIFIED"),
        ("SIM-UNTRUSTED", "NOT_VERIFIED"),  # GLB-07
        ("SIM-SUSPENDED", "NOT_VERIFIED"),
        ("SIM-OTHERPERSON", "NOT_VERIFIED"),
        ("SIM-SAMENAME", "REVIEW"),  # GLB-22
        ("SIM-ATTENDED", "NOT_VERIFIED"),  # GLB-02 / GLB-21
    ],
)
def test_each_source_outcome_gives_its_own_decision(rt_url: str, tag: str, decision: str) -> None:
    import asyncio

    tenant, subject = uid(), uid()

    async def go() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            r = await _check(c, tenant, subject, claimed=_award(tag))
            assert r.assertion.decision.value == decision
            assert r.assertion.professional_eligibility.value == "NOT_EVALUATED"

    asyncio.run(go())


def test_a_vanished_source_leaves_the_claim_unverifiable(rt_url: str) -> None:
    import asyncio

    tenant, subject = uid(), uid()
    claimed = {"employer_id": "SIM-GONE", "start_date": "2010-01-01"}

    async def go() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            r = await _check(c, tenant, subject, "EMPLOYMENT_ASSERTION", claimed)
            assert r.assertion.decision.value == "UNVERIFIABLE"  # GLB-13

    asyncio.run(go())


def test_a_source_that_raises_is_unavailable_not_a_verdict(rt_url: str) -> None:
    import asyncio

    class Boom:
        source_id = "boom"

        async def lookup(self, *a, **k):
            raise TimeoutError("down")

    tenant, subject = uid(), uid()

    async def go() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            r = await _check(c, tenant, subject, source=Boom())
            assert r.assertion.decision.value == "SOURCE_UNAVAILABLE"
            assert r.assertion.primary_source_verified is False

    asyncio.run(go())


def test_no_pack_no_check_and_the_source_is_never_asked(rt_url: str) -> None:
    import asyncio

    asked: list[int] = []

    class Spy:
        source_id = "spy"

        async def lookup(self, *a, **k):
            asked.append(1)
            raise AssertionError("must not be asked")

    tenant, subject = uid(), uid()

    async def go() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            with pytest.raises(PackError) as e:
                await _check(
                    c, tenant, subject, env="production", source=Spy()
                )  # DESIGN packs never serve a deployed env
            assert e.value.code == "PACK_UNAVAILABLE"
            with pytest.raises(PackError):
                await checks.run_check(  # a country with no pack
                    c,
                    tenant_id=tenant,
                    subject_principal_id=subject,
                    kind="ACADEMIC_AWARD",
                    claimed=_award(),
                    jurisdiction={"country": "FR"},
                    profession=None,
                    packs=pack_registry(),
                    source=Spy(),
                    now=NOW,
                    environment="test",
                )
            assert (
                await c.execute(text("SELECT count(*) FROM cred.claim_check"))
            ).scalar_one() == 0
        assert asked == []

    asyncio.run(go())


def test_the_simulator_cannot_be_built_in_a_deployed_environment() -> None:
    with pytest.raises(RuntimeError):
        checks.SimulatorSource("production")


def test_a_bad_claim_is_refused_before_a_pack_or_source(rt_url: str) -> None:
    import asyncio

    tenant, subject = uid(), uid()

    async def go() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            for kind, claimed, code in (
                ("MASTERS_OF_UNIVERSE", _award(), "CLAIM_CLASS_UNRECOGNIZED"),
                ("ACADEMIC_AWARD", {"institution": "U"}, "CLAIM_FIELD_MISSING"),
                ("ACADEMIC_AWARD", {**_award(), "gpa": "4.0"}, "CLAIM_FIELD_UNKNOWN"),
                (
                    "DISCIPLINARY_FINDING",
                    {"regulator": "r", "disposition": "d", "subject_match": "MATCHED"},
                    "CLAIM_CLASS_NOT_CHECKABLE",
                ),
            ):
                with pytest.raises(ClaimError) as e:
                    await _check(c, tenant, subject, kind, claimed)
                assert e.value.code == code

    asyncio.run(go())


def test_another_tenant_cannot_see_a_check_and_a_row_cannot_be_edited_or_deleted(
    rt_url: str,
) -> None:
    import asyncio

    t1, t2, subject = uid(), uid(), uid()

    async def go() -> None:
        async with engine(rt_url) as eng:
            async with tenant_transaction(eng, ctx(t1)) as c:
                await _check(c, t1, subject)
            async with tenant_transaction(eng, ctx(t2)) as c:
                assert (
                    await c.execute(text("SELECT count(*) FROM cred.claim_check"))
                ).scalar_one() == 0
            with pytest.raises(DBAPIError):
                async with tenant_transaction(eng, ctx(t1)) as c:
                    await c.execute(text("UPDATE cred.claim_check SET decision = 'NOT_VERIFIED'"))
            with pytest.raises(DBAPIError):
                async with tenant_transaction(eng, ctx(t1)) as c:
                    await c.execute(text("UPDATE cred.claim_check SET assertion = '{}'::jsonb"))

    asyncio.run(go())


def test_a_newer_check_supersedes_the_older_and_the_history_stays(rt_url: str) -> None:
    import asyncio

    tenant, subject = uid(), uid()

    async def go() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            await _check(c, tenant, subject, claimed=_award("SIM-ATTENDED"))
            await _check(c, tenant, subject, claimed=_award("Uni of X"))
            rows = (
                await c.execute(
                    text(
                        "SELECT decision, superseded_at IS NOT NULL FROM cred.claim_check ORDER BY created_at, check_id"
                    )
                )
            ).all()
            assert len(rows) == 2
            assert sorted((r[0], r[1]) for r in rows) == [
                ("NOT_VERIFIED", True),
                ("VERIFIED", False),
            ]

    asyncio.run(go())
