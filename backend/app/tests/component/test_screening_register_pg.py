"""Register reconciliation, monitoring triggers, delisting replay and appeals on PostgreSQL (ZID-ED-04 sections 04, 06, 07, 08;
EDR04-04 and EDR04-06). Service level: the HTTP side is in test_screening_register_api_pg."""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import timedelta
from typing import Any

import pytest
from conftest import ctx, engine
from sqlalchemy import text
from test_approvals_pg import world  # noqa: F401  (a fixture)
from test_screening_pg import CFG, GRAPH, NOW, OFFICER, load_lists, sign, subjects, uid, un_xml
from zid_db import tenant_transaction
from zid_platform.modules.screening.application import screening as service
from zid_platform.modules.screening.domain.decision import (
    RegisterEvidence,
    RegisterPerson,
    Role,
    Ruling,
    ScreeningError,
    ScreeningStatus,
)
from zid_platform.modules.screening.domain.lists import PartyKind
from zid_platform.modules.screening.domain.matching import Party
from zid_platform.modules.screening.domain.ownership import Edge, Graph, Holder, HolderKind
from zid_platform.modules.screening.infrastructure import store

CLEAN_UN = un_xml(("Aaa", "Bbb"))


def person(ref: str, name: str) -> RegisterPerson:
    return RegisterPerson(
        ref, "PSC", Party(ref, PartyKind.PERSON, name), ("ownership-of-shares-25-to-50-percent",)
    )


def register(*names: str, digest: str = "a") -> RegisterEvidence:
    return RegisterEvidence(
        "OK", tuple(person(f"psc{i}", n) for i, n in enumerate(names, 1)), digest * 64
    )


async def open_it(
    c: Any,
    tenant: str,
    kyb: str,
    *,
    who: list[Any] | None = None,
    reg: RegisterEvidence | None = None,
    key: str = "case-key-0001",
) -> Any:
    return await service.open_case(
        c, tenant, country="GB", subjects=who or subjects(), graph=GRAPH, authority=OFFICER, requested_by=uid(),
        idempotency_key=key, cfg=CFG, sign=sign, now=NOW, kyb_case_id=kyb, register=reg, register_source="companies-house",
    )  # fmt: skip


def test_a_person_the_register_lists_is_a_task_a_new_declaration_clears_it_and_the_earlier_one_stays(
    rt_url: str, owner_url: str
) -> None:
    tenant, kyb = uid(), uid()

    async def run() -> None:
        async with engine(rt_url) as eng, engine(owner_url) as worker:
            await load_lists(eng, un=CLEAN_UN)
            async with tenant_transaction(eng, ctx(tenant)) as c:
                first = await open_it(c, tenant, kyb, reg=register("Olivia Owner", "Peter Hidden"))
                assert first.status is ScreeningStatus.GATHERING
                assert [t["code"] for t in first.tasks] == ["DECLARE_REGISTER_PSC"] and first.tasks[
                    0
                ]["count"] == 1
                body = (await service.history(c, tenant, first.case_id))[-1].body
                assert body["register"]["status"] == "OK" and "Peter" not in json.dumps(body)

                # only a case that is waiting for details can be declared again
                declared = [
                    *subjects(),
                    (Role.OWNER, Party("p-peter", PartyKind.PERSON, "Peter Hidden")),
                ]
                graph = Graph(
                    "co",
                    {
                        "co": Holder("co", HolderKind.ENTITY),
                        "p-owner": Holder("p-owner", HolderKind.PERSON),
                        "p-peter": Holder("p-peter", HolderKind.PERSON),
                    },
                    (
                        Edge("p-owner", "co", GRAPH.edges[0].shares_pct),
                        Edge("p-peter", "co", GRAPH.edges[0].shares_pct),
                    ),
                )
                again = await service.amend_case(
                    c, tenant, kyb, country="GB", subjects=declared, graph=graph, authority=OFFICER, requested_by=uid(), cfg=CFG,
                    sign=sign, now=NOW + timedelta(minutes=1), register=register("Olivia Owner", "Peter Hidden"), register_source="companies-house",
                )  # fmt: skip
                assert (
                    again.status is ScreeningStatus.VERIFIED
                    and again.case_id != first.case_id
                    and again.tasks == ()
                )
                latest = await service.case_for_kyb(c, tenant, kyb, now=NOW)
                assert latest is not None and latest[0].case_id == again.case_id
                assert len(await service.history(c, tenant, first.case_id)) == 1, (
                    "the earlier declaration and its evidence stay"
                )
                with pytest.raises(ScreeningError) as e:
                    await service.amend_case(
                        c,
                        tenant,
                        kyb,
                        country="GB",
                        subjects=declared,
                        graph=graph,
                        authority=OFFICER,
                        requested_by=uid(),
                        cfg=CFG,
                        sign=sign,
                        now=NOW,
                    )
                assert e.value.code == "AMEND_NOT_AVAILABLE"

            # restricted records never reach an event or an audit record
            async with worker.begin() as c:
                events = (
                    (
                        await c.execute(
                            text(
                                "SELECT payload::text FROM events.outbox_event WHERE tenant_id = :t"
                            ),
                            {"t": uuid.UUID(tenant)},
                        )
                    )
                    .scalars()
                    .all()
                )
                audits = (
                    (
                        await c.execute(
                            text(
                                "SELECT body::text FROM evidence.audit_record WHERE tenant_id = :t"
                            ),
                            {"t": uuid.UUID(tenant)},
                        )
                    )
                    .scalars()
                    .all()
                )
            blob = " ".join([*events, *audits])
            assert blob, "something was recorded"
            for name in ("Peter", "Hidden", "Olivia", "Alice"):
                assert name not in blob, name

    asyncio.run(run())


def test_a_changed_register_a_new_owner_or_a_privileged_role_asks_for_a_rescreen_that_names_why(
    rt_url: str,
) -> None:
    tenant, kyb = uid(), uid()

    async def run() -> None:
        async with engine(rt_url) as eng:
            await load_lists(eng, un=un_xml(("Aaa", "Bbb"), ("Peter", "Hidden")))
            async with tenant_transaction(eng, ctx(tenant)) as c:
                v1 = await open_it(c, tenant, kyb, reg=register("Olivia Owner"))
                assert (
                    v1.status is ScreeningStatus.VERIFIED
                    and await service.due_for_rescreen(c, tenant, now=NOW) == []
                )

                # the register now lists a person who is on a list: recorded once, asked for, and answered with the reason named
                assert await service.record_register(
                    c,
                    tenant,
                    v1.case_id,
                    register("Olivia Owner", "Peter Hidden", digest="b"),
                    source="companies-house",
                    now=NOW,
                )
                assert not await service.record_register(
                    c,
                    tenant,
                    v1.case_id,
                    register("Olivia Owner", "Peter Hidden", digest="b"),
                    source="companies-house",
                    now=NOW,
                ), "the same register is nothing new"
                assert await service.due_for_rescreen(c, tenant, now=NOW) == [v1.case_id]
                v2 = await service.rescreen(c, tenant, v1.case_id, cfg=CFG, sign=sign, now=NOW)
                assert v2.status is ScreeningStatus.REVIEW_REQUIRED and v2.version == 2
                body = (await service.history(c, tenant, v1.case_id))[-1].body
                assert body["triggers"] == ["REGISTER_CHANGED"]
                assert any(m["party_ref"] == "reg:psc2" for m in body["matches"]), (
                    "the person the customer never declared was screened"
                )
                assert (
                    await store.open_triggers(c, tenant) == {}
                    and await service.due_for_rescreen(c, tenant, now=NOW) == []
                )

                # a privileged role granted, an owner added or an organization activated: every live case is asked for again
                assert (
                    await service.request_rescreen(
                        c, tenant, reason="PRIVILEGED_ROLE_GRANTED", now=NOW
                    )
                    == 1
                )
                assert await service.due_for_rescreen(c, tenant, now=NOW) == [v1.case_id]
                v3 = await service.rescreen(c, tenant, v1.case_id, cfg=CFG, sign=sign, now=NOW)
                assert v3.version == 3 and (await service.history(c, tenant, v1.case_id))[-1].body[
                    "triggers"
                ] == ["PRIVILEGED_ROLE_GRANTED"]

                # a case that is only waiting for the customer is not asked for
                waiting = await service.open_case(
                    c, tenant, country="GB", subjects=subjects()[:1], graph=None, authority=OFFICER, requested_by=uid(),
                    idempotency_key="case-key-wait", cfg=CFG, sign=sign, now=NOW, kyb_case_id=uid(),
                )  # fmt: skip
                assert waiting.status is ScreeningStatus.GATHERING
                assert (
                    await service.request_rescreen(
                        c, tenant, reason="ORGANIZATION_ACTIVATED", now=NOW
                    )
                    == 1
                )

    asyncio.run(run())


def test_a_delisting_is_traceable_and_every_decision_can_be_replayed_from_what_it_used(
    rt_url: str,
) -> None:
    tenant, kyb = uid(), uid()
    listed = un_xml(("Eric", "Badege"), ("Aaa", "Bbb"))

    async def run() -> None:
        async with engine(rt_url) as eng:
            await load_lists(eng, un=listed)
            async with tenant_transaction(eng, ctx(tenant)) as c:
                v1 = await open_it(c, tenant, kyb, who=subjects(rep="Eric Badege"))
                assert v1.status is ScreeningStatus.REVIEW_REQUIRED, (
                    "a listed name waits for a person"
                )
                reviewer = uid()
                ruled = await service.review(
                    c, tenant, v1.case_id, ruling=Ruling(reviewer, ScreeningStatus.REVIEW_REQUIRED, "Still checking the passport details."),
                    cfg=CFG, sign=sign, now=NOW,
                )  # fmt: skip
                assert ruled.version == 2

            # the person is delisted: a new list version, and the next pass screens the case again
            await load_lists(eng, un=CLEAN_UN)
            async with tenant_transaction(eng, ctx(tenant)) as c:
                assert await service.due_for_rescreen(c, tenant, now=NOW) == [v1.case_id]
                v3 = await service.rescreen(c, tenant, v1.case_id, cfg=CFG, sign=sign, now=NOW)
                assert v3.version == 3
                hist = await service.history(c, tenant, v1.case_id)
                un = {
                    h.version: next(x for x in h.body["lists"] if x["list_id"] == "UN_SC")
                    for h in hist
                }
                assert un[1]["content_hash"] != un[3]["content_hash"], (
                    "each decision names the exact list it used"
                )

                # replay: decide again from what each decision used and compare with the signed evidence
                first = await service.replay(c, tenant, v1.case_id, 1, cfg=CFG)
                assert (
                    first["replayable"]
                    and first["matches"]
                    and first["status"] == "REVIEW_REQUIRED"
                )
                last = await service.replay(c, tenant, v1.case_id, 3, cfg=CFG)
                assert last["replayable"] and last["matches"], last
                assert last["status"] != "REVIEW_REQUIRED" or "SCREENING_ALERT" not in json.dumps(
                    last
                )
                assert (await service.replay(c, tenant, v1.case_id, 2, cfg=CFG)) == {
                    "replayable": False,
                    "reason": "REVIEWER_RULING",
                    "version": 2,
                }
                with pytest.raises(ScreeningError):
                    await service.replay(c, tenant, v1.case_id, 9, cfg=CFG)

    asyncio.run(run())


def test_an_appeal_goes_to_a_different_reviewer_keeps_the_statement_private_and_one_is_open_at_a_time(
    rt_url: str, owner_url: str
) -> None:
    tenant, kyb = uid(), uid()
    secret = "ZQXWVUNIQUESTATEMENT"
    statement = f"My passport shows 1975 and not the year on the list. {secret}"

    async def run() -> None:
        async with engine(rt_url) as eng, engine(owner_url) as worker:
            await load_lists(eng, un=un_xml(("Eric", "Badege"), ("Aaa", "Bbb")))
            reviewer_a, reviewer_b, applicant = uid(), uid(), uid()
            async with tenant_transaction(eng, ctx(tenant)) as c:
                case = await open_it(c, tenant, kyb, who=subjects(rep="Eric Badege"))
                assert case.status is ScreeningStatus.REVIEW_REQUIRED
                # an appeal is for a rejection
                with pytest.raises(ScreeningError) as e:
                    await service.request_appeal(
                        c,
                        tenant,
                        case.case_id,
                        kind="APPEAL",
                        statement=statement,
                        requested_by=applicant,
                        cfg=CFG,
                        sign=sign,
                        now=NOW,
                    )
                assert e.value.code == "APPEAL_NOT_AVAILABLE"
                rejected = await service.review(
                    c,
                    tenant,
                    case.case_id,
                    ruling=Ruling(
                        reviewer_a,
                        ScreeningStatus.REJECTED,
                        "The listed person is the representative.",
                    ),
                    cfg=CFG,
                    sign=sign,
                    now=NOW,
                )
                assert (
                    rejected.status is ScreeningStatus.REJECTED
                    and "appeal" in rejected.next_action.lower()
                )

                appealed = await service.request_appeal(
                    c,
                    tenant,
                    case.case_id,
                    kind="APPEAL",
                    statement=statement,
                    requested_by=applicant,
                    cfg=CFG,
                    sign=sign,
                    now=NOW,
                )
                assert appealed.status is ScreeningStatus.REVIEW_REQUIRED
                with pytest.raises(ScreeningError) as e:
                    await service.request_appeal(
                        c,
                        tenant,
                        case.case_id,
                        kind="CORRECTION",
                        statement=statement,
                        requested_by=applicant,
                        cfg=CFG,
                        sign=sign,
                        now=NOW,
                    )
                assert e.value.code == "APPEAL_ALREADY_OPEN"
                detail = await service.restricted_detail(c, tenant, case.case_id)
                assert [a["statement"] for a in detail["appeals"]] == [statement] and detail[
                    "alerts"
                ], "the reviewer sees the statement and the alerts"

                # whoever rejected it cannot decide the appeal; someone else can, with a written reason
                with pytest.raises(ScreeningError) as e:
                    await service.review(
                        c,
                        tenant,
                        case.case_id,
                        ruling=Ruling(
                            reviewer_a,
                            ScreeningStatus.VERIFIED,
                            "Second look at the same passport.",
                        ),
                        cfg=CFG,
                        sign=sign,
                        now=NOW,
                    )
                assert e.value.code == "REVIEWER_CONFLICT"
                decided = await service.review(
                    c,
                    tenant,
                    case.case_id,
                    ruling=Ruling(
                        reviewer_b,
                        ScreeningStatus.VERIFIED,
                        "The passport confirms a different person.",
                    ),
                    cfg=CFG,
                    sign=sign,
                    now=NOW,
                )
                assert decided.status is ScreeningStatus.VERIFIED
                hist = await service.history(c, tenant, case.case_id)
                assert [h.decision_status.value for h in hist] == [
                    "REVIEW_REQUIRED",
                    "REJECTED",
                    "REVIEW_REQUIRED",
                    "VERIFIED",
                ]
                assert not any(
                    secret in json.dumps(h.body) or secret in " ".join(h.reasons) for h in hist
                ), "only the hash of the statement is in the evidence"

            async with worker.begin() as c:
                events = (
                    (
                        await c.execute(
                            text(
                                "SELECT payload::text FROM events.outbox_event WHERE tenant_id = :t"
                            ),
                            {"t": uuid.UUID(tenant)},
                        )
                    )
                    .scalars()
                    .all()
                )
                audits = (
                    (
                        await c.execute(
                            text(
                                "SELECT body::text FROM evidence.audit_record WHERE tenant_id = :t"
                            ),
                            {"t": uuid.UUID(tenant)},
                        )
                    )
                    .scalars()
                    .all()
                )
            assert not any(secret in x for x in [*events, *audits]) and not any(
                "Badege" in x for x in [*events, *audits]
            )

            # the statement is append-only
            async with engine(rt_url) as eng2, tenant_transaction(eng2, ctx(tenant)) as c:
                with pytest.raises(Exception, match="append-only"):
                    await c.execute(
                        text("UPDATE verify.screening_appeal SET statement = 'changed'")
                    )

    asyncio.run(run())


def test_a_second_tenant_reads_none_of_the_register_triggers_or_appeals(rt_url: str) -> None:
    mine, theirs, kyb = uid(), uid(), uid()

    async def run() -> None:
        async with engine(rt_url) as eng:
            await load_lists(eng, un=CLEAN_UN)
            async with tenant_transaction(eng, ctx(mine)) as c:
                case = await open_it(c, mine, kyb, reg=register("Olivia Owner"))
                await service.request_rescreen(c, mine, reason="PRIVILEGED_ROLE_GRANTED", now=NOW)
            async with tenant_transaction(eng, ctx(theirs)) as c:
                assert await store.load_register(c, theirs, case.case_id) is None
                assert await store.open_triggers(c, theirs) == {}
                assert await store.appeals_for(c, theirs, case.case_id) == []
                assert await service.live_kyb_cases(c) == [], (
                    "row-level security scopes even the worker's query to the tenant in force"
                )

    asyncio.run(run())


def test_the_retry_gap_does_not_fill_the_evidence_when_nothing_changed(rt_url: str) -> None:
    """A trigger is answered once: a pass that finds none and no list change screens nothing."""
    tenant, kyb = uid(), uid()

    async def run() -> None:
        async with engine(rt_url) as eng:
            await load_lists(eng, un=CLEAN_UN)
            async with tenant_transaction(eng, ctx(tenant)) as c:
                case = await open_it(c, tenant, kyb)
                for _ in range(3):
                    assert (
                        await service.due_for_rescreen(c, tenant, now=NOW + timedelta(hours=2))
                        == []
                    )
                assert (await service.history(c, tenant, case.case_id))[-1].version == 1

    asyncio.run(run())


def test_granting_a_privileged_role_asks_for_a_rescreen_of_the_organizations_live_cases(
    world: Any,  # noqa: F811
) -> None:
    """ZID-ED-04 section 07: privilege escalation is a trigger. The executor of the privileged-role grant asks; the worker answers."""
    w = world
    kyb = uid()

    async def seed() -> str:
        async with engine(w.rt_url) as eng:
            await load_lists(eng, un=CLEAN_UN)
            async with tenant_transaction(eng, ctx(w.o.tenant)) as c:
                return str((await open_it(c, w.o.tenant, kyb)).case_id)

    case_id = asyncio.run(seed())
    a1, a2 = w.person(), w.person()
    case = w.create(
        w.o.owner,
        "privileged-role-grant-or-elevation",
        "role:APPROVER",
        {"role": "APPROVER"},
        "New checker",
    ).json()
    assert w.vote(a1, case).status_code == 201 and w.vote(a2, case).status_code == 201
    assert w.execute(w.o.owner, case["case_id"]).json()["outcome"] == "EXECUTED"

    async def triggers() -> dict[str, list[str]]:
        async with engine(w.rt_url) as eng, tenant_transaction(eng, ctx(w.o.tenant)) as c:
            return await store.open_triggers(c, w.o.tenant)

    assert asyncio.run(triggers()) == {case_id: ["PRIVILEGED_ROLE_GRANTED"]}
