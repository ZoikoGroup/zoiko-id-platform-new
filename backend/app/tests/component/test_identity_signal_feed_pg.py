"""Identity signals reach the reviewer queue on PostgreSQL (CR-101; DTV-20, DTV-21, DTV-22): a link group or a shared device opens one review
item with counts only, a repeat adds nothing, a person's ruling closes it, and nothing here blocks or clears anybody."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

from conftest import ctx, engine
from zid_db import tenant_transaction
from zid_platform.modules.screening.application import signals
from zid_platform.modules.verification.application import identity_signal_feed as feed
from zid_platform.modules.verification.domain.identity_signals import Kind, Observation

NOW = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)


def _obs() -> list[Observation]:
    phone = "ph-hash-aaaa"
    device = "dev-hash-bbbb"
    return [
        Observation("alice", Kind.PHONE, phone),
        Observation("bob", Kind.PHONE, phone),
        Observation("alice", Kind.DEVICE, device),
        Observation("bob", Kind.DEVICE, device),
        Observation("carol", Kind.DEVICE, device),
        Observation("erin", Kind.DEVICE, device),
        Observation("frank", Kind.DEVICE, device),
        Observation("dave", Kind.PHONE, "ph-hash-alone"),
    ]


def test_findings_are_deterministic_review_only_and_carry_counts_not_identifiers() -> None:
    a = feed.findings_from(_obs())
    b = feed.findings_from(list(reversed(_obs())))
    assert [(f.rule, f.subject_ref, f.group_key) for f in a] == [
        (f.rule, f.subject_ref, f.group_key) for f in b
    ]
    rules = {f.rule for f in a}
    assert "DEVICE_SHARED_BY_MANY" in rules and any(r.startswith("IDENTITY_LINK_") for r in rules)
    blob = repr(a)
    assert "ph-hash" not in blob and "dev-hash" not in blob  # no identifier, hashed or not
    assert all("dave" != f.subject_ref for f in a)  # a person seen alone is no finding


def test_a_link_group_opens_one_item_a_repeat_adds_nothing_and_only_a_person_closes_it(
    rt_url: str,
) -> None:
    tenant, reviewer = str(uuid.uuid4()), str(uuid.uuid4())

    async def run() -> None:
        async with engine(rt_url) as eng:
            async with tenant_transaction(eng, ctx(tenant)) as c:
                first = await feed.raise_for(c, tenant, _obs(), now=NOW)
            async with tenant_transaction(eng, ctx(tenant)) as c:
                second = await feed.raise_for(c, tenant, _obs(), now=NOW)
            assert first >= 2 and second == 0  # the same group is the same item
            async with tenant_transaction(eng, ctx(tenant)) as c:
                queue = await signals.open_queue(c, tenant)
                assert len(queue) == first
                assert {q.source for q in queue} == {"IDENTITY_SIGNAL"}
                assert all(q.status == "OPEN" for q in queue)
                assert "ph-hash" not in repr([q.detail for q in queue])
                done = await signals.resolve(
                    c,
                    tenant,
                    queue[0].review_id,
                    reviewer_id=reviewer,
                    outcome="CLEARED_BY_REVIEWER",
                    rationale="A family sharing one phone and one tablet.",
                    evidence_ref="doc:household-statement-1",
                    now=NOW,
                )
                assert done.status == "RESOLVED" and done.detail == queue[0].detail

    asyncio.run(run())


def test_one_company_never_sees_another_companys_identity_items(rt_url: str) -> None:
    mine, other = str(uuid.uuid4()), str(uuid.uuid4())

    async def run() -> None:
        async with engine(rt_url) as eng:
            async with tenant_transaction(eng, ctx(mine)) as c:
                await feed.raise_for(c, mine, _obs(), now=NOW)
            async with tenant_transaction(eng, ctx(other)) as c:
                assert await signals.open_queue(c, other) == []

    asyncio.run(run())
