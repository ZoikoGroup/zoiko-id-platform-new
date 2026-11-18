"""Governed release of an official sanctions source on PostgreSQL (CR-113; WF06 S10, QA-006, QA-008, QA-009, QA-010, QA-011).

A staged corpus is invisible to screening until two people other than the stager release it; a bad file is quarantined and the corpus in use stays; a rollback restores an earlier release;
racing publications leave exactly one active corpus; and the table refuses edits to what staging recorded.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from conftest import engine
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from zid_platform.modules.screening.application import source_releases as rel
from zid_platform.modules.screening.domain.decision import ScreeningError
from zid_platform.modules.screening.domain.lists import ListId
from zid_platform.modules.screening.infrastructure import store

NOW = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)
UN_URL = "https://scsanctions.un.org/resources/xml/en/consolidated.xml"


def un_xml(count: int, *, tag: str = "A") -> bytes:
    people = "".join(
        f"<INDIVIDUAL><DATAID>{1000 + i}</DATAID><FIRST_NAME>{tag}{i}</FIRST_NAME><SECOND_NAME>Person</SECOND_NAME>"
        f"<UN_LIST_TYPE>T</UN_LIST_TYPE></INDIVIDUAL>"
        for i in range(count)
    )
    return f"<CONSOLIDATED_LIST><INDIVIDUALS>{people}</INDIVIDUALS><ENTITIES/></CONSOLIDATED_LIST>".encode()


async def stage(c, count=10, tag="A", at=NOW, by="stager", url=UN_URL):
    return await rel.stage(
        c, ListId.UN_SC, {"file": un_xml(count, tag=tag)}, url=url, staged_by=by, now=at
    )


async def release(c, rid, at=NOW, approvers=("p1", "p2"), ack=()):
    for who in approvers:
        await rel.approve(c, rid, approver=who, acknowledged=ack, now=at)
    return await rel.publish(c, rid, now=at)


async def active_state(c):
    versions = (
        await c.execute(
            text(
                "SELECT count(*) FROM verify.sanction_list_version WHERE list_id = 'UN_SC' AND active"
            )
        )
    ).scalar_one()
    releases = (
        await c.execute(
            text(
                "SELECT count(*) FROM verify.sanction_list_release WHERE list_id = 'UN_SC' AND state = 'ACTIVE'"
            )
        )
    ).scalar_one()
    return versions, releases


def test_a_staged_corpus_is_invisible_until_two_other_people_release_it(rt_url: str) -> None:
    async def run() -> None:
        async with engine(rt_url) as eng, eng.begin() as c:
            first = await stage(c)
            assert first["state"] == "VALIDATED" and first["blocked"] == [] and first["flags"] == []
            assert ListId.UN_SC not in await store.snapshots(
                c
            )  # staged is not active: screening cannot read it
            with pytest.raises(ScreeningError) as e:
                await rel.approve(
                    c, first["release_id"], approver="stager", acknowledged=[], now=NOW
                )
            assert e.value.code == "APPROVER_STAGED_THE_RELEASE"
            await rel.approve(c, first["release_id"], approver="p1", acknowledged=[], now=NOW)
            with pytest.raises(ScreeningError) as e2:
                await rel.publish(c, first["release_id"], now=NOW)
            assert (
                e2.value.code == "RELEASE_NOT_PUBLISHABLE"
                and "TWO_APPROVERS_REQUIRED" in e2.value.detail
            )
            assert ListId.UN_SC not in await store.snapshots(c)
            await rel.approve(c, first["release_id"], approver="p2", acknowledged=[], now=NOW)
            done = await rel.publish(c, first["release_id"], now=NOW)
            assert done["state"] == "ACTIVE" and done["activated_at"]
            snap = (await store.snapshots(c))[ListId.UN_SC]
            assert (
                len(snap.entries) == 10 and snap.fetched_at == NOW
            )  # fresh from the release, not from the fetch
            h = await rel.health(
                c, ListId.UN_SC, now=NOW + timedelta(hours=47), max_age=timedelta(hours=48)
            )
            assert h["freshness"] == "FRESH" and h["snapshot"] == first["receipt"]["sha256"]
            stale = await rel.health(
                c, ListId.UN_SC, now=NOW + timedelta(hours=49), max_age=timedelta(hours=48)
            )
            assert stale["freshness"] == "STALE"  # QA-010

    asyncio.run(run())


def test_a_drift_must_be_acknowledged_by_name_and_the_old_release_is_superseded_not_deleted(
    rt_url: str,
) -> None:
    async def run() -> None:
        async with engine(rt_url) as eng, eng.begin() as c:
            one = await stage(c, 10, "A")
            await release(c, one["release_id"])
            two = await stage(c, 40, "B", at=NOW + timedelta(days=1))
            assert two["flags"] == ["COUNT_DRIFT_ABOVE_LIMIT"]
            with pytest.raises(ScreeningError) as e:
                await rel.approve(c, two["release_id"], approver="p3", acknowledged=[], now=NOW)
            assert e.value.code == "FLAGS_NOT_ACKNOWLEDGED"  # QA-008
            diff = await rel.compare(c, two["release_id"])
            assert (
                diff["added"] == 30 and diff["removed"] == 0 and diff["changed"] == 10
            )  # ten references are shared and renamed
            await release(
                c,
                two["release_id"],
                at=NOW + timedelta(days=1),
                approvers=("p3", "p4"),
                ack=["COUNT_DRIFT_ABOVE_LIMIT"],
            )
            assert await active_state(c) == (1, 1)
            states = {r["release_id"]: r["state"] for r in await rel.history(c, ListId.UN_SC)}
            assert (
                states[one["release_id"]] == "SUPERSEDED" and states[two["release_id"]] == "ACTIVE"
            )
            assert len((await store.snapshots(c))[ListId.UN_SC].entries) == 40

    asyncio.run(run())


def test_a_bad_file_is_quarantined_and_the_corpus_in_use_stays(rt_url: str) -> None:
    async def run() -> None:
        async with engine(rt_url) as eng, eng.begin() as c:
            good = await stage(c, 10, "A")
            await release(c, good["release_id"])
            bad = await rel.stage(
                c,
                ListId.UN_SC,
                {"file": b"<CONSOLIDATED_LIST><INDIVIDUALS><INDIVIDUAL>"},
                url=UN_URL,
                staged_by="stager",
                now=NOW,
            )
            assert (
                bad["state"] == "QUARANTINED" and "SCHEMA_INVALID" in bad["blocked"]
            )  # QA-006: truncated
            off = await stage(c, 12, "C", url="https://mirror.example.com/consolidated.xml")
            assert (
                off["state"] == "QUARANTINED" and "HOST_NOT_THE_PUBLISHERS" in off["blocked"]
            )  # QA-007
            for q in (bad, off):
                with pytest.raises(ScreeningError) as e:
                    await rel.approve(c, q["release_id"], approver="p9", acknowledged=[], now=NOW)
                assert e.value.code == "RELEASE_NOT_APPROVABLE"
                with pytest.raises(ScreeningError):
                    await rel.publish(c, q["release_id"], now=NOW)
                with pytest.raises(ScreeningError) as cmp:
                    await rel.compare(c, q["release_id"])
                assert cmp.value.code == "RELEASE_HAS_NO_CORPUS"
            assert len((await store.snapshots(c))[ListId.UN_SC].entries) == 10  # untouched
            assert await active_state(c) == (1, 1)
            with pytest.raises(ScreeningError) as src:
                await rel.stage(c, ListId.LOCAL, {"file": b"x"}, url=UN_URL, staged_by="s", now=NOW)
            assert (
                src.value.code == "SOURCE_NOT_REGISTERED"
            )  # only the official sources are staged this way

    asyncio.run(run())


def test_a_rollback_needs_two_other_people_and_restores_the_earlier_corpus(rt_url: str) -> None:
    async def run() -> None:
        async with engine(rt_url) as eng, eng.begin() as c:
            one = await stage(c, 10, "A")
            await release(c, one["release_id"])
            two = await stage(c, 11, "B", at=NOW + timedelta(days=1))
            await release(c, two["release_id"], at=NOW + timedelta(days=1), approvers=("p3", "p4"))
            assert len((await store.snapshots(c))[ListId.UN_SC].entries) == 11
            at = NOW + timedelta(days=2)
            with pytest.raises(ScreeningError) as e:
                await rel.approve_rollback(c, one["release_id"], approver="stager", now=at)
            assert e.value.code == "APPROVER_STAGED_THE_RELEASE"
            await rel.approve_rollback(c, one["release_id"], approver="p9", now=at)
            with pytest.raises(ScreeningError) as dup:
                await rel.approve_rollback(c, one["release_id"], approver="p9", now=at)
            assert dup.value.code == "APPROVER_ALREADY_APPROVED"
            with pytest.raises(ScreeningError) as e2:
                await rel.rollback(c, one["release_id"], now=at)
            assert e2.value.code == "TWO_APPROVERS_REQUIRED"
            await rel.approve_rollback(c, one["release_id"], approver="p8", now=at)
            back = await rel.rollback(c, one["release_id"], now=at)  # QA-011
            assert back["state"] == "ACTIVE"
            assert len((await store.snapshots(c))[ListId.UN_SC].entries) == 10
            assert await active_state(c) == (1, 1)
            states = {r["release_id"]: r["state"] for r in await rel.history(c, ListId.UN_SC)}
            assert states[two["release_id"]] == "SUPERSEDED"

    asyncio.run(run())


def test_racing_publications_leave_exactly_one_active_corpus(rt_url: str) -> None:
    async def run() -> None:
        async with engine(rt_url) as eng:
            async with eng.begin() as c:
                base = await stage(c, 10, "A")
                await release(c, base["release_id"])
                a = await stage(c, 10, "B", at=NOW + timedelta(days=1))
                b = await stage(c, 10, "C", at=NOW + timedelta(days=1))
                for r in (a, b):
                    for who in ("p1", "p2"):
                        await rel.approve(
                            c, r["release_id"], approver=who, acknowledged=[], now=NOW
                        )

            async def publish(rid: str) -> str:
                async with eng.begin() as c:
                    return (await rel.publish(c, rid, now=NOW + timedelta(days=2)))["state"]

            outcomes = await asyncio.gather(
                publish(a["release_id"]), publish(b["release_id"]), return_exceptions=True
            )
            assert [o for o in outcomes if isinstance(o, BaseException)] == []
            async with eng.begin() as c:
                assert await active_state(c) == (1, 1)  # QA-009: never two

    asyncio.run(run())


def test_the_table_refuses_edits_to_what_staging_recorded_and_a_deletion(
    rt_url: str, owner_url: str
) -> None:
    async def run() -> None:
        async with engine(rt_url) as eng, eng.begin() as c:
            one = await stage(c, 10, "A")
            await rel.approve(c, one["release_id"], approver="p1", acknowledged=[], now=NOW)
        for sql in (
            "UPDATE verify.sanction_list_release SET staged_by = 'someone else'",
            "UPDATE verify.sanction_list_release SET receipt = '{}'::jsonb",
            'UPDATE verify.sanction_list_release SET validation = \'{"blocked": ["EDITED"], "flags": []}\'::jsonb',
            "DELETE FROM verify.sanction_list_release",
        ):
            refused = False
            async with engine(owner_url) as owner:
                try:
                    async with owner.begin() as c:
                        await c.execute(text(sql))
                except DBAPIError:
                    refused = True
            assert refused, f"not refused: {sql}"
        async with engine(rt_url) as eng, eng.begin() as c:
            bad = await rel.stage(
                c, ListId.UN_SC, {"file": b"nope"}, url=UN_URL, staged_by="s", now=NOW
            )
        async with engine(owner_url) as owner:
            with pytest.raises(DBAPIError):
                async with owner.begin() as c:
                    await c.execute(
                        text(
                            "UPDATE verify.sanction_list_release SET state = 'VALIDATED' WHERE release_id = CAST(:r AS uuid)"
                        ),
                        {"r": bad["release_id"]},
                    )  # a quarantined release is never released

    asyncio.run(run())
