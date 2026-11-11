"""Policy pack lifecycle service against PostgreSQL (ZID-RCG-003 RCG-G01): draft, sign, publish, revise, roll back, resolve.

The service is the only way the API will reach the packs; these tests run it end to end through the real tables and triggers.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable
from typing import Any

import pytest
from conftest import engine
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_policy_packs_pg import NOW, PEOPLE, _draft
from zid_platform.modules.verification.application import policy_packs as svc
from zid_platform.modules.verification.domain.policy_packs import (
    APPROVER_ROLES,
    Outcome,
    PackError,
    PackStatus,
    PolicyPack,
    Rules,
    pack_hash,
)
from zid_platform.modules.verification.infrastructure import policy_pack_store as store


def _market() -> dict[str, Any]:
    """A product code of its own, so packs left by other tests in the shared database never meet this test's resolver."""
    return {"product": f"P-{uuid.uuid4().hex[:12]}", "purpose": "ENGAGEMENT"}


def _run(url: str, work: Any) -> Any:
    async def go() -> Any:
        async with engine(url) as eng, eng.begin() as c:
            return await work(c)

    return asyncio.run(go())


async def _publish(c: Any, pack: PolicyPack) -> PolicyPack:
    if pack.version == 1:  # a revision was already stored as a draft by `revise_pack`
        await svc.open_draft(c, pack, now=NOW)
    for role in APPROVER_ROLES:
        await svc.sign(
            c,
            pack.pack_id,
            pack.version,
            role=role,
            approver=PEOPLE[role],
            signed_hash=pack_hash(pack),
            now=NOW,
        )
    return await svc.publish_pack(c, pack.pack_id, pack.version, now=NOW)


def _governing(c: Any, m: dict[str, Any], **extra: Any) -> Awaitable[Any]:
    return svc.governing_pack(
        c, country="GB", at=NOW, product=m["product"], purpose=m["purpose"], **extra
    )


def test_the_whole_lifecycle_through_the_service(rt_url: str) -> None:
    m = _market()
    pack = _draft(**m)

    async def work(c: Any) -> tuple[Any, Any, Any, Any]:
        published = await _publish(c, pack)
        ok = await _governing(c, m, subdivision="GB-ENG", profession="nurse")
        other_area = await _governing(c, m, subdivision="GB-SCT", profession="nurse")
        other_country = await svc.governing_pack(c, country="FR", at=NOW, **m)
        return published, ok, other_area, other_country

    published, ok, other_area, other_country = _run(rt_url, work)
    assert published.status is PackStatus.APPROVED
    assert (ok.outcome, ok.pack.pack_id) == (Outcome.APPLICABLE, pack.pack_id)
    assert (other_area.outcome, other_area.reason) == (Outcome.REVIEW, "policy_missing")
    assert (other_country.outcome, other_country.reason) == (
        Outcome.UNAVAILABLE,
        "market_not_approved",
    )


def test_the_service_refuses_what_the_rules_refuse_and_leaves_no_trace(rt_url: str) -> None:
    m = _market()
    pack = _draft(**m)
    _run(rt_url, lambda c: svc.open_draft(c, pack, now=NOW))

    def refused(work: Any, code: str) -> None:
        with pytest.raises(PackError) as e:
            _run(rt_url, work)
        assert e.value.code == code

    def sign(role: str, approver: str, signed: str) -> Any:
        return lambda c: svc.sign(
            c, pack.pack_id, 1, role=role, approver=approver, signed_hash=signed, now=NOW
        )

    refused(sign("legal", "maker", pack_hash(pack)), "PACK_APPROVER_IS_AUTHOR")
    refused(sign("legal", "lena", "0" * 64), "PACK_APPROVAL_HASH_MISMATCH")
    refused(sign("intern", "lena", pack_hash(pack)), "PACK_APPROVER_ROLE_UNKNOWN")
    refused(
        lambda c: svc.sign(
            c, "no-such-pack", 1, role="legal", approver="lena", signed_hash="0" * 64, now=NOW
        ),
        "PACK_NOT_FOUND",
    )
    _run(rt_url, sign("legal", "lena", pack_hash(pack)))
    refused(sign("grc", "lena", pack_hash(pack)), "PACK_APPROVER_NOT_DISTINCT")
    refused(
        lambda c: svc.publish_pack(c, pack.pack_id, 1, now=NOW), "PACK_NOT_PUBLISHABLE"
    )  # one of four approvals
    refused(lambda c: svc.open_draft(c, pack, now=NOW), "PACK_EXISTS")

    still = _run(rt_url, lambda c: store.load(c, pack.pack_id, 1))
    assert still.status is PackStatus.DRAFT and list(still.approvals) == ["legal"]
    nothing = _run(rt_url, lambda c: _governing(c, m, subdivision="GB-ENG", profession="nurse"))
    assert nothing.outcome is Outcome.UNAVAILABLE


def test_a_revision_is_a_new_draft_and_rolling_back_is_retiring_it(rt_url: str) -> None:
    m = _market()
    v1 = _draft(**m)
    new_rules = Rules(evidence_minimum=("LICENSE_STANDING", "INSURANCE"))

    async def governing_version(c: Any) -> int:
        r = await _governing(c, m, subdivision="GB-ENG", profession="nurse")
        assert r.pack is not None
        return r.pack.version

    async def revise_and_publish(c: Any) -> tuple[int, int]:
        await _publish(c, v1)
        v2 = await svc.revise_pack(c, v1.pack_id, author="maker2", rules=new_rules, now=NOW)
        assert (v2.version, v2.status, dict(v2.approvals)) == (2, PackStatus.DRAFT, {})
        await _publish(c, v2)
        return 2, await governing_version(c)

    assert _run(rt_url, revise_and_publish) == (2, 2)

    async def rollback(c: Any) -> tuple[int, PolicyPack | None, PolicyPack | None]:
        await svc.retire_pack(c, v1.pack_id, 2, now=NOW)
        return (
            await governing_version(c),
            await store.load(c, v1.pack_id, 1),
            await store.load(c, v1.pack_id, 2),
        )

    version, old, retired = _run(rt_url, rollback)
    assert version == 1  # the last approved version governs again
    assert (
        old is not None and old.status is PackStatus.APPROVED and old.rules == v1.rules
    )  # never rewritten
    assert retired is not None and retired.status is PackStatus.RETIRED


def test_one_open_draft_per_pack_and_no_empty_revision(rt_url: str) -> None:
    m = _market()
    v1 = _draft(**m)
    _run(rt_url, lambda c: _publish(c, v1))
    _run(
        rt_url,
        lambda c: svc.revise_pack(
            c, v1.pack_id, author="maker2", rules=Rules(evidence_minimum=("X",)), now=NOW
        ),
    )

    def code_of(work: Any) -> str:
        with pytest.raises(PackError) as e:
            _run(rt_url, work)
        return str(e.value.code)

    assert (
        code_of(
            lambda c: svc.revise_pack(
                c, v1.pack_id, author="m3", rules=Rules(evidence_minimum=("Y",)), now=NOW
            )
        )
        == "PACK_DRAFT_OPEN"
    )
    other = _draft(**_market())
    _run(rt_url, lambda c: _publish(c, other))
    assert (
        code_of(lambda c: svc.revise_pack(c, other.pack_id, author="m3", now=NOW))
        == "PACK_NO_CHANGE"
    )
    assert (
        code_of(
            lambda c: svc.revise_pack(c, other.pack_id, author="m3", rules=other.rules, now=NOW)
        )
        == "PACK_NO_CHANGE"
    )
    assert (
        code_of(lambda c: svc.revise_pack(c, "missing", author="m3", rules=other.rules, now=NOW))
        == "PACK_NOT_FOUND"
    )
    assert (
        code_of(lambda c: svc.retire_pack(c, v1.pack_id, 2, now=NOW)) == "PACK_NOT_APPROVED"
    )  # v2 is still a draft


def test_a_tampered_published_pack_stops_the_decision_loudly(rt_url: str, owner_url: str) -> None:
    m = _market()
    pack = _draft(**m)
    _run(rt_url, lambda c: _publish(c, pack))

    async def tamper(
        c: Any,
    ) -> None:  # only the table owner can; the trigger that forbids it is switched off for the edit
        await c.execute(text("ALTER TABLE verify.policy_pack DISABLE TRIGGER policy_pack_guard"))
        try:
            await c.execute(
                text(
                    "UPDATE verify.policy_pack SET rules = jsonb_set(rules, '{lawful_basis}', '\"nothing\"') WHERE pack_id = :p"
                ),
                {"p": pack.pack_id},
            )
        finally:
            await c.execute(text("ALTER TABLE verify.policy_pack ENABLE TRIGGER policy_pack_guard"))

    _run(owner_url, tamper)
    with pytest.raises(PackError) as e:
        _run(rt_url, lambda c: _governing(c, m, subdivision="GB-ENG", profession="nurse"))
    assert e.value.code == "PACK_STORED_HASH_MISMATCH"  # not skipped, not a pass


def test_the_database_still_refuses_what_the_service_would_let_through(rt_url: str) -> None:
    """The service is not the only guard: skipping it and writing the table directly is refused too."""
    m = _market()
    pack = _draft(**m)
    _run(rt_url, lambda c: svc.open_draft(c, pack, now=NOW))
    with pytest.raises(DBAPIError) as e:
        _run(rt_url, lambda c: store.mark_published(c, pack.pack_id, 1, now=NOW))
    assert "four approvals" in str(e.value)
