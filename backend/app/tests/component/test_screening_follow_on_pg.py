"""What a new list release does to what already relied on the old one (WF06 QA-011, QA-019, QA-032, QA-050; ENG-14; CR-113), on PostgreSQL.

A case that was cleared as a false positive on an entry that is later delisted goes back to a hold as a new revision by the system, the earlier decisions stay, the chain still verifies, and an active watch on
the source is asked to screen again whatever its cadence says. A release that changes nothing does neither."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

from conftest import ctx, engine
from sqlalchemy import text
from test_screening_requests_pg import (
    MAX_AGE,
    NOW,
    load_lists,
    open_case,
    pack_for,
    person,
    proposal,
    uid,
)
from test_screening_source_release_pg import un_xml
from zid_db import tenant_transaction
from zid_platform.modules.screening.application import screening_requests as sr
from zid_platform.modules.screening.application import source_releases as rel
from zid_platform.modules.screening.application import watches
from zid_platform.modules.screening.domain import coverage as cov
from zid_platform.modules.screening.domain import disposition as disp
from zid_platform.modules.screening.domain import monitoring_watch as mw
from zid_platform.modules.screening.domain.lists import ListId
from zid_platform.modules.screening.infrastructure import store
from zid_platform.modules.screening.infrastructure.config import parse as parse_config

UN_URL = "https://scsanctions.un.org/resources/xml/en/consolidated.xml"
CFG = parse_config(
    {"version": "t", "policy": {"required_lists": ["UN_SC"], "list_max_age_hours": 48}}
)


def badege() -> bytes:
    return (
        b"<CONSOLIDATED_LIST><INDIVIDUALS><INDIVIDUAL><DATAID>900</DATAID><FIRST_NAME>Eric</FIRST_NAME><SECOND_NAME>Badege</SECOND_NAME>"
        b"<UN_LIST_TYPE>T</UN_LIST_TYPE></INDIVIDUAL></INDIVIDUALS><ENTITIES/></CONSOLIDATED_LIST>"
    )


async def release(
    c: Any, files: bytes, at: Any, by: str, approvers: tuple[str, str], ack: tuple[str, ...] = ()
) -> dict[str, Any]:
    staged = await rel.stage(c, ListId.UN_SC, {"file": files}, url=UN_URL, staged_by=by, now=at)
    for who in approvers:
        await rel.approve(c, staged["release_id"], approver=who, acknowledged=list(ack), now=at)
    return await rel.publish(c, staged["release_id"], now=at)


def test_a_delisting_reopens_the_cases_that_relied_on_it_and_asks_the_watches_to_screen_again(
    rt_url: str,
) -> None:
    tenant, creator = uid(), uid()

    async def run() -> None:
        async with engine(rt_url) as eng:
            await load_lists(
                eng,
                at=NOW - timedelta(hours=1),
                only=(
                    ListId.OFAC_SDN,
                    ListId.OFAC_NON_SDN,
                    ListId.UK_SANCTIONS,
                    ListId.EU_CONSOLIDATED,
                ),
            )
            async with eng.begin() as c:
                first = await release(c, badege(), NOW - timedelta(hours=1), "stager", ("p1", "p2"))
                assert (
                    first["cases_reopened"] == 0 and first["watches_asked"] == 0
                )  # the first release has nothing before it

            async with tenant_transaction(eng, ctx(tenant)) as c:
                hit = await open_case(c, tenant, "key-follow-hit1", person("Eric Badege"))
                clear = await open_case(c, tenant, "key-follow-clear", person("Alice Wonderland"))
                cleared = proposal(
                    hit["request_id"], cov.Disposition.CLEARED_FOR_PURPOSE, disp.Action.RELEASE
                )
                await sr.decide(
                    c,
                    hit["request_id"],
                    cleared,
                    checker="senior-1",
                    checker_role=disp.Role.SENIOR,
                    now=NOW,
                )
                w = (
                    await watches.create_watch(
                        c,
                        clear["request_id"],
                        created_by=creator,
                        cadence=timedelta(days=30),
                        triggers=frozenset({mw.Trigger.SCHEDULE, mw.Trigger.LIST_UPDATE}),
                        now=NOW,
                    )
                )["watch_id"]
                for who in ("p1", "p2"):
                    await watches.approve_watch(c, w, approver=who)
                await watches.activate_watch(c, w, gates_signed=True)
                await _ran_once(c, tenant, w)
                assert (
                    await watches.due_watches(c, tenant, now=NOW + timedelta(hours=2)) == []
                )  # inside its cadence

            # a release that changes nothing leaves everything alone
            async with eng.begin() as c:
                same = await release(
                    c,
                    badege(),
                    NOW + timedelta(hours=1),
                    "stager",
                    ("p3", "p4"),
                    ack=("IDENTICAL_TO_ACTIVE",),
                )
                assert same["cases_reopened"] == 0 and same["watches_asked"] == 0

            # a release that no longer lists the entry
            async with eng.begin() as c:
                later = await release(
                    c,
                    un_xml(2, tag="Z"),
                    NOW + timedelta(hours=3),
                    "stager",
                    ("p5", "p6"),
                    ack=("COUNT_DRIFT_ABOVE_LIMIT",),
                )
                assert (
                    later["cases_reopened"] >= 1
                )  # QA-019, QA-050: the cleared case relied on the delisted entry (a delisting reopens every closed case that relied on it, in any tenant, so other tests' cases may be counted too)
                assert (
                    later["watches_asked"] == 1
                )  # QA-032: the watch on the source is asked to screen again

            async with tenant_transaction(eng, ctx(tenant)) as c:
                detail = await sr.restricted_detail(
                    c, hit["request_id"], now=NOW + timedelta(hours=3), max_age=MAX_AGE
                )
                assert detail["state"] == "REVIEW_REQUIRED" and detail["chain_intact"] is True
                kinds = [(d["revision"], d["kind"], d["disposition"]) for d in detail["decisions"]]
                assert kinds == [
                    (1, "DECISION", "CLEARED_FOR_PURPOSE"),
                    (2, "REOPEN", "HOLD_PENDING_REVIEW"),
                ]  # the earlier decision stays
                assert detail["decisions"][1]["maker"] == disp.SYSTEM and detail["decisions"][1][
                    "reason_codes"
                ] == ["ENTRY_DELISTED_OR_CHANGED"]
                view = await sr.customer_view(
                    c, hit["request_id"], now=NOW + timedelta(hours=3), max_age=MAX_AGE
                )
                assert view["finding"] == "Review pending"
                assert (
                    await sr.customer_view(
                        c, clear["request_id"], now=NOW + timedelta(hours=3), max_age=MAX_AGE
                    )
                )["finding"]  # the other case is untouched
                assert await watches.due_watches(c, tenant, now=NOW + timedelta(hours=3)) == [
                    w
                ]  # due whatever the cadence says
                await _ran_once(c, tenant, w, at=NOW + timedelta(hours=4))
                assert (
                    await watches.due_watches(c, tenant, now=NOW + timedelta(hours=4)) == []
                )  # the request is spent by the run
                n = (
                    await c.execute(
                        text(
                            "SELECT count(*) FROM verify.screening_request_decision WHERE request_id = CAST(:r AS uuid)"
                        ),
                        {"r": clear["request_id"]},
                    )
                ).scalar_one()
                assert (
                    n == 0
                )  # a case that was never decided is not given a reopening it does not need

    asyncio.run(run())


async def _ran_once(c: Any, tenant: str, watch_id: str, at: Any = None) -> None:
    await watches.run_watch(
        c,
        watch_id,
        pack=pack_for(tenant),
        snapshots=await store.snapshots(c),
        max_age=MAX_AGE,
        match_policy=CFG.policy.match,
        identity=_identity(tenant),
        now=at or NOW,
    )


def _identity(tenant: str) -> Any:
    from zid_platform.modules.screening.domain import eligibility as elig

    return elig.IdentityClaim("IDENTITY_VERIFIED_FOR_SCOPE", NOW - timedelta(days=2), tenant)
