"""A company's owners screened as people and rolled up, on PostgreSQL (WF06 QA-029 to QA-033; CR-120): the latest case per person counts, a possible match holds the company in review, and an unscreened owner is never clear."""

from __future__ import annotations

import asyncio
from datetime import timedelta

from conftest import ctx, engine
from test_screening_requests_pg import (
    NOW,
    load_lists,
    open_case,
    person,
    request,
    uid,
)
from zid_db import tenant_transaction
from zid_platform.modules.screening.application import ownership_screening as os_
from zid_platform.modules.screening.domain import ownership_rollup as rr


def test_the_company_rolls_up_its_owners_latest_cases_and_never_reads_a_missing_one_as_clear(
    rt_url: str,
) -> None:
    tenant = uid()
    clear_owner, hit_owner, unscreened_owner = uid(), uid(), uid()

    async def run() -> None:
        async with engine(rt_url) as eng:
            await load_lists(eng, at=NOW)
            async with tenant_transaction(eng, ctx(tenant)) as c:
                await open_case(
                    c,
                    tenant,
                    "k-own-clear",
                    person("Alice Wonderland"),
                    request=request(tenant, subject_ref=clear_owner),
                )
                await open_case(
                    c,
                    tenant,
                    "k-own-hit",
                    person("Eric Badege"),
                    request=request(tenant, subject_ref=hit_owner),
                )

                owners = [
                    rr.Owner(clear_owner, "CONFIRMED"),
                    rr.Owner(hit_owner, "NEEDS_CONFIRMATION"),
                    rr.Owner(unscreened_owner, "NEEDS_CONFIRMATION"),
                ]
                screens = await os_.screens_of(c, [o.person_ref for o in owners])
                assert {s.person_ref: s.case_state for s in screens} == {
                    clear_owner: "NO_CANDIDATE",
                    hit_owner: "REVIEW_REQUIRED",
                }

                out = await os_.company_rollup(c, owners, now=NOW, max_age=timedelta(days=30))
                assert out.status is rr.Rollup.IN_REVIEW  # a possible match waits for a person
                by = {v.person_ref: v.reason for v in out.owners}
                assert by == {
                    clear_owner: "OWNER_CLEAR_FOR_SCOPE",
                    hit_owner: "OWNER_CASE_IN_REVIEW",
                    unscreened_owner: "OWNER_NOT_SCREENED",
                }
                assert out.designated == frozenset()  # a possible match is not a designation

                assert await os_.owners_needing_a_case(
                    c, owners, now=NOW, max_age=timedelta(days=30)
                ) == (unscreened_owner,)

                # once every owner has a current clear screen the company is complete clear
                clear_only = [rr.Owner(clear_owner, "CONFIRMED")]
                assert (
                    await os_.company_rollup(c, clear_only, now=NOW, max_age=timedelta(days=30))
                ).status is rr.Rollup.COMPLETE_CLEAR
                # and the same screen a month and a day later is stale, not clear
                stale = await os_.company_rollup(
                    c, clear_only, now=NOW + timedelta(days=31), max_age=timedelta(days=30)
                )
                assert stale.status is rr.Rollup.INCOMPLETE and stale.reasons == (
                    "OWNER_CHECK_STALE",
                )

    asyncio.run(run())
