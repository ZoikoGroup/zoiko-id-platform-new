"""Market releases in PostgreSQL (ZID-RCG-003 RCG-G10): the lifecycle through the store, and the rules the database keeps on its own.

The database refuses freezing with a gate missing, evidence from the author, evidence after the freeze, an approval from the author or from
anyone who recorded evidence, an approval of another hash, a release that is not fully approved, a second live release for one market, and any
edit of a frozen release. A release edited behind the application's back is refused when it is read back.
"""

from __future__ import annotations

import asyncio
import dataclasses
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from conftest import engine
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from zid_platform.modules.verification.domain import market_release as md
from zid_platform.modules.verification.infrastructure import market_release_store as store

NOW = datetime(2026, 11, 12, tzinfo=UTC)
PACK = md.PackRef("gb-individual", 1, "a" * 64)
RECORDERS = {
    "legal": "lena",
    "regulatory_ops": "ravi",
    "security": "sam",
    "privacy": "pia",
    "qa": "quinn",
    "vendor_mgmt": "vera",
}
APPROVERS = {"legal": "al", "security_grc": "gus", "qa": "qa-lead", "product_owner": "pat"}


def _market() -> md.MarketKey:
    return md.MarketKey(
        "GB", f"P-{uuid.uuid4().hex[:12]}", "STANDARD"
    )  # its own product, so no other test's release shares the market


def _rid() -> str:
    return f"rel-{uuid.uuid4().hex[:10]}"


def _run(url: str, work: Any) -> Any:
    async def go() -> Any:
        async with engine(url) as eng, eng.begin() as c:
            return await work(c)

    return asyncio.run(go())


def _refused(url: str, work: Any, *fragments: str) -> None:
    with pytest.raises(DBAPIError) as e:
        _run(url, work)
    assert any(f in str(e.value) for f in fragments), str(e.value)


async def _draft(c: Any, market: md.MarketKey, rid: str, *, gates: int = 7) -> md.MarketRelease:
    r = md.new_release(rid, market, author="maker")
    await store.insert_draft(c, r, now=NOW)
    for g in md.GATES[:gates]:
        r = md.record_gate(
            r,
            gate=g.gate_id,
            status=md.GateStatus.PASSED,
            evidence_ref=f"EVID-{g.gate_id}",
            role=g.owner_role,
            by=RECORDERS[g.owner_role],
            at=NOW,
        )
        await store.add_gate_result(c, r, r.results[-1])
    return r


async def _frozen(c: Any, market: md.MarketKey, rid: str) -> md.MarketRelease:
    f = md.freeze(await _draft(c, market, rid), (PACK,))
    await store.freeze(c, f, now=NOW)
    return f


async def _releaseable(c: Any, market: md.MarketKey, rid: str) -> md.MarketRelease:
    f = await _frozen(c, market, rid)
    for role in md.RELEASE_APPROVER_ROLES:
        f = md.approve_release(
            f, role=role, approver=APPROVERS[role], signed_hash=f.release_hash or ""
        )
        await store.add_approval(c, f, role=role, now=NOW)
    await store.set_status(c, f.release_id, f.version, md.ReleaseStatus.RELEASEABLE, now=NOW)
    return f


def test_a_release_goes_from_draft_to_launched_and_opens_its_market(rt_url: str) -> None:
    market, rid = _market(), _rid()

    async def work(c: Any) -> tuple[md.MarketRelease | None, list[md.MarketRelease]]:
        r = await _releaseable(c, market, rid)
        await store.set_status(c, rid, r.version, md.ReleaseStatus.LAUNCHED, now=NOW)
        return await store.load(c, rid, 1), await store.load_market(c, market)

    loaded, market_releases = _run(rt_url, work)
    assert loaded is not None and loaded.status is md.ReleaseStatus.LAUNCHED
    assert loaded.release_hash == md.compute_hash(loaded) and loaded.pack_refs == (PACK,)
    assert (
        sorted(loaded.approvals) == sorted(md.RELEASE_APPROVER_ROLES) and len(loaded.results) == 7
    )
    assert md.market_decision(market_releases, market, [PACK]).open
    assert not md.market_decision(
        market_releases, market, [md.PackRef("gb-individual", 2, "b" * 64)]
    ).open  # a pack changed after the release


def test_the_database_refuses_freezing_without_all_seven_gates(rt_url: str) -> None:
    market, rid = _market(), _rid()

    async def work(c: Any) -> None:
        r = await _draft(c, market, rid, gates=3)
        fake = dataclasses.replace(
            r, status=md.ReleaseStatus.FROZEN, pack_refs=(PACK,), release_hash="c" * 64
        )  # skips the domain's check
        await store.freeze(c, fake, now=NOW)

    _refused(rt_url, work, "seven gates")


def test_the_database_refuses_freezing_with_no_pack_pinned(rt_url: str) -> None:
    market, rid = _market(), _rid()

    async def work(c: Any) -> None:
        r = await _draft(c, market, rid)
        await store.freeze(
            c,
            dataclasses.replace(
                r, status=md.ReleaseStatus.FROZEN, pack_refs=(), release_hash="c" * 64
            ),
            now=NOW,
        )

    _refused(rt_url, work, "policy pack pinned")


def test_the_database_refuses_evidence_from_the_author_and_evidence_after_the_freeze(
    rt_url: str,
) -> None:
    market, rid = _market(), _rid()
    _run(rt_url, lambda c: _frozen(c, market, rid))

    async def late(c: Any) -> None:  # the release is frozen: no more evidence
        await c.execute(
            text(
                "INSERT INTO verify.market_release_gate (release_id, version, gate, status, evidence_ref, role, recorded_by, recorded_at) "
                "VALUES (:id, 1, 'RG-01', 'PASSED', 'EVID-LATE', 'legal', 'lena', :now)"
            ),
            {"id": rid, "now": NOW},
        )

    _refused(rt_url, late, "only while the release is a draft")

    market2, rid2 = _market(), _rid()

    async def by_author(c: Any) -> None:
        r = md.new_release(rid2, market2, author="maker")
        await store.insert_draft(c, r, now=NOW)
        await c.execute(
            text(
                "INSERT INTO verify.market_release_gate (release_id, version, gate, status, evidence_ref, role, recorded_by, recorded_at) "
                "VALUES (:id, 1, 'RG-01', 'PASSED', 'EVID-1', 'legal', 'maker', :now)"
            ),
            {"id": rid2, "now": NOW},
        )

    _refused(rt_url, by_author, "author of a release records no evidence")


def test_the_database_refuses_an_approval_from_the_author_an_evidence_recorder_a_wrong_hash_or_a_repeat(
    rt_url: str,
) -> None:
    market, rid = _market(), _rid()
    frozen = _run(rt_url, lambda c: _frozen(c, market, rid))
    h = frozen.release_hash

    def approval(approver: str, hash_: str | None, role: str = "legal") -> Any:
        async def work(c: Any) -> None:
            await c.execute(
                text(
                    "INSERT INTO verify.market_release_approval (release_id, version, role, approver, release_hash, approved_at) "
                    "VALUES (:id, 1, :r, :a, :h, :now)"
                ),
                {"id": rid, "r": role, "a": approver, "h": hash_, "now": NOW},
            )

        return work

    _refused(rt_url, approval("maker", h), "author of a release cannot approve")
    _refused(rt_url, approval("lena", h), "recorded evidence")  # an evidence recorder
    _refused(rt_url, approval("al", "0" * 64), "hash of this release")
    _run(rt_url, approval("al", h, "legal"))
    _refused(rt_url, approval("al", h, "qa"), "duplicate key", "unique")  # one person, one approval


def test_the_database_refuses_an_approval_before_the_freeze_and_a_release_that_is_not_fully_approved(
    rt_url: str,
) -> None:
    market, rid = _market(), _rid()

    async def early(c: Any) -> None:  # a draft is not approved
        await _draft(c, market, rid)
        await c.execute(
            text(
                "INSERT INTO verify.market_release_approval (release_id, version, role, approver, release_hash, approved_at) "
                "VALUES (:id, 1, 'legal', 'al', :h, :now)"
            ),
            {"id": rid, "h": "d" * 64, "now": NOW},
        )

    _refused(rt_url, early, "only a frozen release can be approved")

    market2, rid2 = _market(), _rid()

    async def partial(c: Any) -> None:  # three of four approvals
        f = await _frozen(c, market2, rid2)
        for role in ("legal", "security_grc", "qa"):
            f = md.approve_release(
                f, role=role, approver=APPROVERS[role], signed_hash=f.release_hash or ""
            )
            await store.add_approval(c, f, role=role, now=NOW)
        await store.set_status(c, rid2, 1, md.ReleaseStatus.RELEASEABLE, now=NOW)

    _refused(rt_url, partial, "four approvals")


def test_a_frozen_release_cannot_change_move_out_of_order_or_be_deleted_and_evidence_and_approvals_are_append_only(
    rt_url: str, owner_url: str
) -> None:
    market, rid = _market(), _rid()
    _run(rt_url, lambda c: _releaseable(c, market, rid))

    def sql(statement: str) -> Any:
        async def work(c: Any) -> None:
            await c.execute(text(statement), {"id": rid})

        return work

    where = "WHERE release_id = :id AND version = 1"
    _refused(
        rt_url,
        sql(f"UPDATE verify.market_release SET pack_refs = '[]'::jsonb {where}"),
        "cannot be changed",
    )
    _refused(
        rt_url,
        sql(f"UPDATE verify.market_release SET author = 'someone' {where}"),
        "cannot be changed",
    )
    _refused(
        rt_url, sql(f"UPDATE verify.market_release SET status = 'DRAFT' {where}"), "moves only"
    )
    _refused(
        owner_url,
        sql(f"DELETE FROM verify.market_release {where}"),
        "never deleted",
        "foreign key",
        "violates",
    )
    _refused(
        rt_url,
        sql(f"UPDATE verify.market_release_gate SET status = 'FAILED' {where}"),
        "append-only",
    )
    _refused(
        rt_url,
        sql(f"UPDATE verify.market_release_approval SET approver = 'x' {where}"),
        "append-only",
    )


def test_a_market_has_at_most_one_live_release(rt_url: str) -> None:
    market = _market()

    async def first(c: Any) -> None:
        r = await _releaseable(c, market, _rid())
        await store.set_status(c, r.release_id, 1, md.ReleaseStatus.LAUNCHED, now=NOW)

    _run(rt_url, first)

    async def second(c: Any) -> None:
        r = await _releaseable(c, market, _rid())
        await store.set_status(c, r.release_id, 1, md.ReleaseStatus.LAUNCHED, now=NOW)

    _refused(rt_url, second, "market_release_one_live", "duplicate key")


def test_withdrawing_closes_the_market_and_frees_it_for_a_new_release(rt_url: str) -> None:
    market, rid = _market(), _rid()

    async def work(c: Any) -> list[md.MarketRelease]:
        r = await _releaseable(c, market, rid)
        await store.set_status(c, rid, 1, md.ReleaseStatus.LAUNCHED, now=NOW)
        await store.set_status(c, rid, 1, md.ReleaseStatus.WITHDRAWN, now=NOW)
        again = await _releaseable(c, market, _rid())
        await store.set_status(
            c, again.release_id, 1, md.ReleaseStatus.LAUNCHED, now=NOW
        )  # allowed once the first is withdrawn
        assert r.version == 1
        return await store.load_market(c, market)

    releases = _run(rt_url, work)
    assert sorted(r.status.value for r in releases) == ["LAUNCHED", "WITHDRAWN"]
    assert md.market_decision(releases, market, [PACK]).open


def test_a_release_edited_behind_the_applications_back_is_refused_when_read_and_restored_afterwards(
    rt_url: str, owner_url: str
) -> None:
    market, rid = _market(), _rid()
    _run(rt_url, lambda c: _releaseable(c, market, rid))

    async def set_refs(refs: str) -> str:
        async with engine(owner_url) as eng, eng.begin() as c:
            old = (
                await c.execute(
                    text(
                        "SELECT pack_refs::text FROM verify.market_release WHERE release_id = :id"
                    ),
                    {"id": rid},
                )
            ).scalar_one()
            await c.execute(
                text("ALTER TABLE verify.market_release DISABLE TRIGGER market_release_guard")
            )
            try:
                await c.execute(
                    text(
                        "UPDATE verify.market_release SET pack_refs = CAST(:r AS jsonb) WHERE release_id = :id"
                    ),
                    {"r": refs, "id": rid},
                )
            finally:
                await c.execute(
                    text("ALTER TABLE verify.market_release ENABLE TRIGGER market_release_guard")
                )
        return str(old)

    original = asyncio.run(
        set_refs('[{"pack_id": "other", "version": 9, "pack_hash": "' + "e" * 64 + '"}]')
    )
    try:
        with pytest.raises(md.ReleaseError) as e:
            _run(rt_url, lambda c: store.load(c, rid, 1))
        assert e.value.code == "RELEASE_STORED_HASH_MISMATCH"
    finally:
        asyncio.run(
            set_refs(original)
        )  # the shared database keeps no corrupted release for the tests that run next
    assert _run(rt_url, lambda c: store.load(c, rid, 1)) is not None


def test_releases_are_listed_newest_first_and_can_be_filtered(rt_url: str) -> None:
    market, rid = _market(), _rid()

    async def work(c: Any) -> list[md.MarketRelease]:
        await _frozen(c, market, rid)
        return await store.list_releases(c, country="GB", status="FROZEN", limit=500)

    mine = [r for r in _run(rt_url, work) if r.market == market]
    assert [(r.release_id, r.status) for r in mine] == [(rid, md.ReleaseStatus.FROZEN)]
