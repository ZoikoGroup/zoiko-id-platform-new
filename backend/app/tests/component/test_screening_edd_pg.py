"""EDD information requests stored on PostgreSQL (WF06 section 10; CR-121): one open request per case, a partial answer stays open, someone else reviews, the worker records reminders and expires what ran out, and expiry clears nothing."""

from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta

import pytest
from conftest import ctx, engine
from sqlalchemy import text
from test_screening_requests_pg import NOW, load_lists, open_case, person, uid
from zid_db import tenant_transaction
from zid_platform.modules.screening.application import edd
from zid_platform.modules.screening.domain.decision import ScreeningError


def test_a_request_is_opened_answered_and_reviewed_by_someone_else_and_never_says_why(
    rt_url: str,
) -> None:
    tenant, analyst, senior = uid(), uid(), uid()

    async def run() -> None:
        async with engine(rt_url) as eng:
            await load_lists(eng, at=NOW)
            async with tenant_transaction(eng, ctx(tenant)) as c:
                case = (await open_case(c, tenant, "key-edd-1", person("Eric Badege")))[
                    "request_id"
                ]
                opened = await edd.open_request(
                    c,
                    tenant,
                    case,
                    ["SOURCE_OF_FUNDS", "SUPPORTING_DOCUMENT"],
                    requested_by=analyst,
                    due_in_days=20,
                    now=NOW,
                )
                assert opened["state"] == "REQUESTED" and opened["still_needed"] == [
                    "SOURCE_OF_FUNDS",
                    "SUPPORTING_DOCUMENT",
                ]
                for word in ("sanction", "pep", "politically", "media", "screening", "risk"):
                    assert word not in opened["message"].lower(), word

                with pytest.raises(ScreeningError) as again:
                    await edd.open_request(
                        c,
                        tenant,
                        case,
                        ["SOURCE_OF_FUNDS"],
                        requested_by=analyst,
                        due_in_days=10,
                        now=NOW,
                    )
                assert again.value.code == "REQUEST_ALREADY_OPEN"  # one open request per case

                with pytest.raises(ScreeningError) as bad:
                    await edd.open_request(
                        c,
                        tenant,
                        case,
                        ["BANK_PASSWORD"],
                        requested_by=analyst,
                        due_in_days=10,
                        now=NOW,
                    )
                assert bad.value.code == "ITEMS_INVALID"

                partial = await edd.receive(
                    c,
                    opened["edd_id"],
                    ["SOURCE_OF_FUNDS", "NOT_ASKED"],
                    now=NOW + timedelta(days=2),
                )
                assert partial["state"] == "REQUESTED" and partial["still_needed"] == [
                    "SUPPORTING_DOCUMENT"
                ]
                done = await edd.receive(
                    c, opened["edd_id"], ["SUPPORTING_DOCUMENT"], now=NOW + timedelta(days=3)
                )
                assert done["state"] == "RECEIVED" and done["still_needed"] == []

                with pytest.raises(ScreeningError) as own:
                    await edd.review(
                        c,
                        opened["edd_id"],
                        reviewer=analyst,
                        accepted=True,
                        code=None,
                        now=NOW + timedelta(days=4),
                    )
                assert own.value.code == "REVIEWER_ASKED_FOR_IT"
                with pytest.raises(ScreeningError) as nocode:
                    await edd.review(
                        c,
                        opened["edd_id"],
                        reviewer=senior,
                        accepted=False,
                        code=None,
                        now=NOW + timedelta(days=4),
                    )
                assert nocode.value.code == "REVIEW_CODE_REQUIRED"
                accepted = await edd.review(
                    c,
                    opened["edd_id"],
                    reviewer=senior,
                    accepted=True,
                    code=None,
                    now=NOW + timedelta(days=4),
                )
                assert accepted["state"] == "ACCEPTED"

                # accepted, a new request can be opened for the same case
                assert (
                    await edd.open_request(
                        c,
                        tenant,
                        case,
                        ["SOURCE_OF_WEALTH"],
                        requested_by=analyst,
                        due_in_days=10,
                        now=NOW + timedelta(days=5),
                    )
                )["state"] == "REQUESTED"

    asyncio.run(run())


def test_the_worker_records_reminders_and_expires_what_ran_out_without_touching_the_case(
    rt_url: str,
) -> None:
    tenant, analyst = uid(), uid()

    async def run() -> None:
        async with engine(rt_url) as eng:
            await load_lists(eng, at=NOW)
            async with tenant_transaction(eng, ctx(tenant)) as c:
                case = (await open_case(c, tenant, "key-edd-2", person("Eric Badege")))[
                    "request_id"
                ]
                req = await edd.open_request(
                    c,
                    tenant,
                    case,
                    ["SOURCE_OF_FUNDS"],
                    requested_by=analyst,
                    due_in_days=20,
                    now=NOW,
                )
                state_before = (
                    await c.execute(
                        text(
                            "SELECT state FROM verify.screening_request WHERE request_id = CAST(:r AS uuid)"
                        ),
                        {"r": case},
                    )
                ).scalar_one()

                async def reminders() -> int:
                    return (
                        await c.execute(
                            text(
                                "SELECT jsonb_array_length(reminders) FROM verify.screening_edd_request WHERE edd_id = CAST(:i AS uuid)"
                            ),
                            {"i": req["edd_id"]},
                        )
                    ).scalar_one()

                # the worker pass is cross-tenant, so other tests' requests may be ticked too: read this request's own row, not the totals
                await edd.tick_all(c, NOW + timedelta(days=5))
                assert await reminders() == 0
                await edd.tick_all(c, NOW + timedelta(days=10))
                assert await reminders() == 1
                await edd.tick_all(c, NOW + timedelta(days=11))
                assert await reminders() == 1  # the same mark does not remind twice

                await edd.tick_all(c, NOW + timedelta(days=21))
                row = (
                    await c.execute(
                        text(
                            "SELECT state FROM verify.screening_edd_request WHERE edd_id = CAST(:i AS uuid)"
                        ),
                        {"i": req["edd_id"]},
                    )
                ).one()
                assert row.state == "EXPIRED"
                with pytest.raises(ScreeningError) as late:
                    await edd.receive(
                        c, req["edd_id"], ["SOURCE_OF_FUNDS"], now=NOW + timedelta(days=22)
                    )
                assert late.value.code == "REQUEST_CLOSED"

                # expiry never clears the case: its state is exactly what it was
                state_after = (
                    await c.execute(
                        text(
                            "SELECT state FROM verify.screening_request WHERE request_id = CAST(:r AS uuid)"
                        ),
                        {"r": case},
                    )
                ).scalar_one()
                assert state_after == state_before == "REVIEW_REQUIRED"

    asyncio.run(run())


def test_another_tenants_request_is_invisible_and_unknown_ids_are_refused(rt_url: str) -> None:
    tenant, other, analyst = uid(), uid(), uid()

    async def run() -> None:
        async with engine(rt_url) as eng:
            await load_lists(eng, at=NOW)
            async with tenant_transaction(eng, ctx(tenant)) as c:
                case = (await open_case(c, tenant, "key-edd-3", person("Eric Badege")))[
                    "request_id"
                ]
                req = await edd.open_request(
                    c,
                    tenant,
                    case,
                    ["SOURCE_OF_FUNDS"],
                    requested_by=analyst,
                    due_in_days=20,
                    now=NOW,
                )
            async with tenant_transaction(eng, ctx(other)) as c:
                with pytest.raises(ScreeningError) as e1:
                    await edd.receive(c, req["edd_id"], ["SOURCE_OF_FUNDS"], now=NOW)
                assert e1.value.code == "REQUEST_NOT_FOUND"
                with pytest.raises(ScreeningError) as e2:
                    await edd.receive(c, str(uuid.uuid4()), ["SOURCE_OF_FUNDS"], now=NOW)
                assert (
                    e2.value.code == "REQUEST_NOT_FOUND"
                )  # the same answer for another tenant's request and one that never was

    asyncio.run(run())
