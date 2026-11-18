"""The outbox notice a screening case produces, on PostgreSQL (WF06; CR-120): one thin event per change, in the case's own tenant, carrying nothing a check found."""

from __future__ import annotations

import asyncio
from typing import Any

from conftest import ctx, engine
from sqlalchemy import text
from test_screening_requests_pg import NOW, load_lists, open_case, person, proposal, uid
from zid_db import tenant_transaction
from zid_platform.modules.screening.application import screening_requests as sr
from zid_platform.modules.screening.domain import coverage as cov
from zid_platform.modules.screening.domain import disposition as disp

NAMES = ("Eric Badege", "Badege", "UN_SC", "SDGT", "candidate", "matched", "score")


def test_a_case_emits_one_thin_notice_per_change_and_never_what_a_check_found(rt_url: str) -> None:
    tenant, other = uid(), uid()

    async def run() -> list[tuple[str, int, dict[str, Any]]]:
        async with engine(rt_url) as eng:
            await load_lists(eng, at=NOW)
            async with tenant_transaction(eng, ctx(tenant)) as c:
                hit = await open_case(c, tenant, "key-hook-1", person("Eric Badege"))
                await sr.decide(
                    c, hit["request_id"],
                    proposal(hit["request_id"], cov.Disposition.CLEARED_FOR_PURPOSE, disp.Action.RELEASE),
                    checker="senior-1", checker_role=disp.Role.SENIOR, now=NOW,
                )  # fmt: skip
                rows = await c.execute(
                    text(
                        "SELECT event_type, aggregate_version, payload FROM events.outbox_event "
                        "WHERE aggregate_type = 'screening_case' AND aggregate_id = :i ORDER BY aggregate_version"
                    ),
                    {"i": hit["request_id"]},
                )
                got = [(r[0], r[1], dict(r[2]).get("data", dict(r[2]))) for r in rows]
            async with tenant_transaction(eng, ctx(other)) as c:
                leaked = (
                    await c.execute(
                        text(
                            "SELECT count(*) FROM events.outbox_event WHERE aggregate_type = 'screening_case' AND aggregate_id = :i"
                        ),
                        {"i": hit["request_id"]},
                    )
                ).scalar_one()
                assert leaked == 0  # another tenant's endpoints are never told
            return got

    got = asyncio.run(run())
    assert [g[0] for g in got] == ["com.zoiko.id.screening.case.outcome_changed.v1"] * 2
    first, second = got[0][2], got[1][2]
    assert (
        first["outcome"] == "IN_REVIEW" and first["revision"] == 0
    )  # a candidate holds the case for a person
    assert second["outcome"] == "COMPLETE" and second["revision"] == got[1][1]
    for _, _, data in got:
        assert set(data) == {"case_id", "market", "outcome", "revision"}
        for word in NAMES:
            assert word not in repr(data), word
