"""Policy packs in PostgreSQL (ZID-RCG-003 RCG-G01): the lifecycle through the store, and the rules the database keeps on its own.

A published version never changes, approvals are append-only, the author and a repeat approver are refused, and a row edited
behind the application's back is refused when read back (T03, T36, T37 at the storage level).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from conftest import engine
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from zid_platform.modules.verification.domain.policy_packs import (
    APPROVER_ROLES,
    LegalSource,
    Outcome,
    PackError,
    PackKey,
    PackStatus,
    PolicyPack,
    Rules,
    SourceStatus,
    approve,
    decision_stamp,
    pack_hash,
    publish,
    resolve,
    revise,
    stamp_matches,
)
from zid_platform.modules.verification.infrastructure import policy_pack_store as store

NOW = datetime(2026, 11, 10, tzinfo=UTC)
PEOPLE = {"legal": "lena", "grc": "gus", "security": "sam", "business_owner": "bea"}


def _draft(pack_id: str | None = None, **key: Any) -> PolicyPack:
    k: dict[str, Any] = {
        "country": "GB",
        "product": "PROFESSIONAL_ELIGIBILITY",
        "purpose": "ENGAGEMENT",
        "subdivision": "GB-ENG",
        "profession": "nurse",
    }
    k.update(key)
    return PolicyPack(
        pack_id=pack_id or f"gb-eng-nurse-{uuid.uuid4().hex[:8]}",
        key=PackKey(**k),
        version=1,
        status=PackStatus.DRAFT,
        sources=(
            LegalSource(
                instrument="Example Act 2024",
                url="https://example.test/act",
                status=SourceStatus.LAW,
                retrieved_at=NOW - timedelta(days=1),
                effective_from=NOW - timedelta(days=30),
                effective_to=None,
                owner="legal-team",
            ),
        ),
        rules=Rules(
            evidence_minimum=("LICENSE_STANDING",),
            alternate_routes=("MANUAL",),
            lawful_basis="contract",
        ),
        author="maker",
    )


def _signed(pack: PolicyPack) -> PolicyPack:
    for role in APPROVER_ROLES:
        pack = approve(pack, role=role, approver=PEOPLE[role], signed_hash=pack_hash(pack))
    return pack


async def _store_draft(conn: Any, pack: PolicyPack, *, approvals: bool = True) -> None:
    await store.insert_draft(conn, pack, now=NOW)
    if approvals:
        for role in pack.approvals:
            await store.add_approval(conn, pack, role=role, now=NOW)


def _refused(coro: Any, *fragments: str) -> None:
    with pytest.raises(DBAPIError) as e:
        asyncio.run(coro)
    assert any(f in str(e.value) for f in fragments), str(e.value)


def test_a_pack_goes_from_draft_to_approved_and_resolves(rt_url: str) -> None:
    pack = _signed(_draft())

    async def go() -> tuple[PolicyPack | None, list[PolicyPack]]:
        async with engine(rt_url) as eng, eng.begin() as c:
            await _store_draft(c, pack)
            await store.mark_published(c, pack.pack_id, 1, now=NOW)
            return (
                await store.load(c, pack.pack_id, 1),
                await store.load_approved(
                    c, country="gb", product=pack.key.product, purpose=pack.key.purpose
                ),
            )

    loaded, approved = asyncio.run(go())
    assert loaded is not None and loaded.status is PackStatus.APPROVED
    assert pack_hash(loaded) == pack_hash(pack) and dict(loaded.approvals) == dict(pack.approvals)
    assert loaded.sources == pack.sources and loaded.rules == pack.rules
    mine = [p for p in approved if p.pack_id == pack.pack_id]
    assert [p.version for p in mine] == [1]
    r = resolve(
        mine,
        country="GB",
        product=pack.key.product,
        purpose=pack.key.purpose,
        at=NOW,
        subdivision="GB-ENG",
        profession="nurse",
    )
    assert (r.outcome, r.pack) == (Outcome.APPLICABLE, mine[0])
    assert (
        publish(pack, at=NOW).status is PackStatus.APPROVED
    )  # the domain agrees with what the store did


def test_the_database_refuses_publishing_without_the_four_approvals(rt_url: str) -> None:
    draft = _draft()
    pack = approve(
        draft, role="legal", approver="lena", signed_hash=pack_hash(draft)
    )  # one of four

    async def go() -> None:
        async with engine(rt_url) as eng, eng.begin() as c:
            await _store_draft(c, pack)
            await store.mark_published(c, pack.pack_id, 1, now=NOW)

    _refused(go(), "four approvals")


def test_the_database_refuses_the_author_a_repeat_approver_and_a_wrong_hash(rt_url: str) -> None:
    pack = _draft()

    async def approval(approver: str, hash_: str, role: str = "grc") -> None:
        async with engine(rt_url) as eng, eng.begin() as c:
            await c.execute(
                text(
                    "INSERT INTO verify.policy_pack_approval (pack_id, version, role, approver, pack_hash, approved_at) "
                    "VALUES (:p, 1, :r, :a, :h, :n)"
                ),
                {"p": pack.pack_id, "r": role, "a": approver, "h": hash_, "n": NOW},
            )

    async def insert() -> None:
        async with engine(rt_url) as eng, eng.begin() as c:
            await store.insert_draft(c, pack, now=NOW)

    asyncio.run(insert())
    _refused(approval("maker", pack_hash(pack)), "author")
    _refused(approval("lena", "0" * 64), "hash of this version")
    asyncio.run(approval("lena", pack_hash(pack), role="legal"))
    _refused(approval("lena", pack_hash(pack), role="grc"), "duplicate key", "unique")


def test_a_published_version_cannot_change_and_only_moves_to_retired(
    rt_url: str, owner_url: str
) -> None:
    pack = _signed(_draft())

    async def publish_it() -> None:
        async with engine(rt_url) as eng, eng.begin() as c:
            await _store_draft(c, pack)
            await store.mark_published(c, pack.pack_id, 1, now=NOW)

    async def sql(url: str, statement: str) -> None:
        async with engine(url) as eng, eng.begin() as c:
            await c.execute(text(statement), {"p": pack.pack_id})

    asyncio.run(publish_it())
    p = "WHERE pack_id = :p AND version = 1"
    _refused(
        sql(
            rt_url,
            f"UPDATE verify.policy_pack SET rules = '{{\"evidence_minimum\": []}}'::jsonb {p}",
        ),
        "cannot be changed",
    )
    _refused(
        sql(rt_url, f"UPDATE verify.policy_pack SET author = 'someone' {p}"), "cannot be changed"
    )
    _refused(
        sql(owner_url, f"DELETE FROM verify.policy_pack {p}"),
        "never deleted",
        "foreign key",
        "violates",
    )
    _refused(
        sql(rt_url, f"UPDATE verify.policy_pack_approval SET approver = 'x' {p}"), "append-only"
    )
    _refused(
        sql(rt_url, f"UPDATE verify.policy_pack SET status = 'DRAFT', published_at = NULL {p}"),
        "cannot be changed",
        "violates",
    )

    async def retire() -> None:
        async with engine(rt_url) as eng, eng.begin() as c:
            await store.mark_retired(c, pack.pack_id, 1, now=NOW)

    asyncio.run(retire())
    _refused(
        sql(rt_url, f"UPDATE verify.policy_pack SET status = 'APPROVED', retired_at = NULL {p}"),
        "APPROVED to RETIRED",
        "violates",
    )


def test_a_draft_cannot_be_retired_and_a_published_version_is_not_published_twice(
    rt_url: str,
) -> None:
    pack = _signed(_draft())

    async def go() -> None:
        async with engine(rt_url) as eng, eng.begin() as c:
            await _store_draft(c, pack)
            await store.mark_published(c, pack.pack_id, 1, now=NOW)

    asyncio.run(go())

    async def again() -> None:
        async with engine(rt_url) as eng, eng.begin() as c:
            await store.mark_published(c, pack.pack_id, 1, now=NOW)

    with pytest.raises(PackError) as e:
        asyncio.run(again())
    assert e.value.code == "PACK_NOT_DRAFT"

    other = _signed(_draft())

    async def retire_draft() -> None:
        async with engine(rt_url) as eng, eng.begin() as c:
            await _store_draft(c, other)
            await store.mark_retired(c, other.pack_id, 1, now=NOW)

    with pytest.raises(PackError) as e2:
        asyncio.run(retire_draft())
    assert e2.value.code == "PACK_NOT_APPROVED"


def test_a_row_edited_behind_the_applications_back_is_refused_when_read(
    rt_url: str, owner_url: str
) -> None:
    pack = (
        _draft()
    )  # a draft may still be edited in the table; the stored hash will no longer match

    async def insert() -> None:
        async with engine(rt_url) as eng, eng.begin() as c:
            await store.insert_draft(c, pack, now=NOW)

    async def tamper() -> None:
        async with engine(owner_url) as eng, eng.begin() as c:
            await c.execute(
                text(
                    "UPDATE verify.policy_pack SET rules = jsonb_set(rules, '{lawful_basis}', '\"nothing\"') WHERE pack_id = :p"
                ),
                {"p": pack.pack_id},
            )

    async def read() -> None:
        async with engine(rt_url) as eng, eng.begin() as c:
            await store.load(c, pack.pack_id, 1)

    asyncio.run(insert())
    asyncio.run(tamper())
    with pytest.raises(PackError) as e:
        asyncio.run(read())
    assert e.value.code == "PACK_STORED_HASH_MISMATCH"


def test_t37_a_new_version_leaves_the_old_decision_stamp_valid(rt_url: str) -> None:
    v1 = _signed(_draft())
    v2 = _signed(revise(v1, author="maker2", rules=Rules(evidence_minimum=("INSURANCE",))))
    stamp = decision_stamp(publish(v1, at=NOW))

    async def go() -> tuple[PolicyPack | None, PolicyPack | None]:
        async with engine(rt_url) as eng, eng.begin() as c:
            await _store_draft(c, v1)
            await store.mark_published(c, v1.pack_id, 1, now=NOW)
            await _store_draft(c, v2)
            await store.mark_published(c, v2.pack_id, 2, now=NOW)
            await store.mark_retired(c, v1.pack_id, 1, now=NOW)
            return await store.load(c, v1.pack_id, 1), await store.load(c, v1.pack_id, 2)

    old, new = asyncio.run(go())
    assert old is not None and new is not None
    assert old.status is PackStatus.RETIRED and new.status is PackStatus.APPROVED
    assert stamp_matches(old, stamp) and not stamp_matches(new, stamp)
