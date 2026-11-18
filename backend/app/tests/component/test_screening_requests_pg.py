"""The WF06 screening case on PostgreSQL (CR-113; QA-012, QA-018, QA-021, QA-035, QA-036, QA-039, QA-040, QA-049).

Refused cases store nothing; a case clears only against every required source; a candidate holds the case and is restricted; the customer never sees a list, a name or a reason; decisions are an
append-only chain under maker and checker; a stale or missing source is unavailable, never clear; one tenant cannot read another's case; a reviewer's queue lists across tenants without a name."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from conftest import ctx, engine
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from zid_db import tenant_transaction
from zid_platform.modules.screening.application import screening as lists_service
from zid_platform.modules.screening.application import screening_requests as sr
from zid_platform.modules.screening.domain import coverage as cov
from zid_platform.modules.screening.domain import disposition as disp
from zid_platform.modules.screening.domain import eligibility as elig
from zid_platform.modules.screening.domain.decision import ScreeningError
from zid_platform.modules.screening.domain.lists import ListId, PartyKind
from zid_platform.modules.screening.domain.matching import MatchPolicy, Party
from zid_platform.modules.screening.infrastructure import store
from zid_platform.modules.screening.infrastructure.market_policies import parse

NOW = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)
MAX_AGE = timedelta(hours=48)
SDN = b'1,"DOE, Norman","individual","SDGT",-0- ,-0- ,-0- ,-0- ,-0- ,-0- ,-0- ,"DOB 1950"\n'
UK = b"Unique ID,Name 1,Name 6,Name type\nUK1,Pavel,Nobody,Primary Name\n"
EU = b'<export><sanctionEntity logicalId="1"><subjectType code="person"/><nameAlias wholeName="Quentin Imaginary"/></sanctionEntity></export>'
UN = (
    b"<CONSOLIDATED_LIST><INDIVIDUALS><INDIVIDUAL><DATAID>900</DATAID><FIRST_NAME>Eric</FIRST_NAME><SECOND_NAME>Badege</SECOND_NAME>"
    b"<UN_LIST_TYPE>T</UN_LIST_TYPE></INDIVIDUAL></INDIVIDUALS><ENTITIES/></CONSOLIDATED_LIST>"
)


def uid() -> str:
    return str(uuid.uuid4())


def pack_for(*tenants: str):
    return parse(
        {
            "markets": {
                "GB": {
                    "version": "gb-1",
                    "purposes": ["KYB"],
                    "regions": ["uk-1"],
                    "subject_types": ["BUSINESS", "UBO", "INDIVIDUAL"],
                    "classes": {"SANCTIONS": "OFFICIAL"},
                    "legal_basis": "lawful-basis-ref-1",
                    "signed_by": ["legal-owner", "aml-owner"],
                    "valid_from": "2026-01-01T00:00:00Z",
                    "enabled_tenants": list(tenants),
                }
            }
        }
    )["GB"]


def request(tenant: str, **kw: Any) -> elig.Request:
    base = {
        "tenant_id": tenant,
        "market": "GB",
        "purpose": "KYB",
        "region": "uk-1",
        "subject_type": elig.SubjectType.UBO,
        "identity": elig.IdentityClaim(
            "IDENTITY_VERIFIED_FOR_SCOPE", NOW - timedelta(days=3), tenant
        ),
        "requested": frozenset({cov.Klass.SANCTIONS}),
    }
    base.update(kw)
    return elig.Request(**base)


async def load_lists(eng: Any, *, at: datetime, only: tuple[ListId, ...] | None = None) -> None:
    files = {
        ListId.OFAC_SDN: {"sdn": SDN},
        ListId.OFAC_NON_SDN: {"sdn": SDN},
        ListId.UK_SANCTIONS: {"file": UK},
        ListId.EU_CONSOLIDATED: {"file": EU},
        ListId.UN_SC: {"file": UN},
    }
    async with eng.begin() as c:
        for list_id, f in files.items():
            if only is None or list_id in only:
                await lists_service.ingest_list(
                    c, list_id, f, fetched_at=at, source_ref="t", now=at
                )


async def open_case(c: Any, tenant: str, key: str, party: Party, **kw: Any) -> dict[str, Any]:
    args = {
        "actor": uid(),
        "idempotency_key": key,
        "request": request(tenant),
        "subject": party,
        "pack": pack_for(tenant),
        "snapshots": await store.snapshots(c),
        "max_age": MAX_AGE,
        "match_policy": MatchPolicy(),
        "now": NOW,
    }
    args.update(kw)
    return await sr.open_request(c, tenant, **args)


def person(name: str, **kw: Any) -> Party:
    return Party("p1", PartyKind.PERSON, name, **kw)


def proposal(
    case_id: str, disposition, action, maker="analyst-1", role=disp.Role.ANALYST, rev=0, **kw: Any
) -> disp.Proposal:
    base = {
        "case_id": case_id,
        "expected_revision": rev,
        "disposition": disposition,
        "action": action,
        "maker": maker,
        "maker_role": role,
        "rationale": "The date of birth and the country on the list entry both differ from the subject.",
        "reason_codes": ("CONFLICTING_DOB",),
        "evidence_refs": ("ev_1",),
        "policy_version": "gb-1",
        "review_by": NOW + timedelta(days=90),
    }
    base.update(kw)
    return disp.Proposal(**base)


def test_refusals_store_nothing_and_name_every_reason(rt_url: str) -> None:
    tenant, other = uid(), uid()

    async def run() -> None:
        async with engine(rt_url) as eng:
            await load_lists(eng, at=NOW - timedelta(hours=1))
            async with tenant_transaction(eng, ctx(tenant)) as c:
                cases = (
                    ("NO_POLICY_FOR_MARKET", {"pack": None}),
                    (
                        "CLASS_NOT_ENTITLED",
                        {"pack": pack_for(other)},
                    ),  # a pack that does not name this tenant
                    ("PENDING_IDENTITY", {"request": request(tenant, identity=None)}),
                    ("REGION_NOT_APPROVED", {"request": request(tenant, region="us-east")}),
                    ("PURPOSE_NOT_APPROVED", {"request": request(tenant, purpose="MARKETING")}),
                )
                for code, kw in cases:
                    with pytest.raises(ScreeningError) as e:
                        await open_case(c, tenant, f"key-{code}", person("Eric Badege"), **kw)
                    assert e.value.code == "SCREENING_NOT_ELIGIBLE" and code in e.value.detail, code
                n = (
                    await c.execute(text("SELECT count(*) FROM verify.screening_request"))
                ).scalar_one()
                assert n == 0  # QA-036: nothing was stored for a refusal

    asyncio.run(run())


def test_a_clear_case_reads_calibrated_and_a_candidate_holds_it_restricted(rt_url: str) -> None:
    tenant = uid()

    async def run() -> None:
        async with engine(rt_url) as eng:
            await load_lists(eng, at=NOW - timedelta(hours=1))
            async with tenant_transaction(eng, ctx(tenant)) as c:
                clear = await open_case(c, tenant, "key-clear-1", person("Alice Wonderland"))
                assert clear["finding"] == "No candidate was found in the checked sources"
                assert (
                    clear["pep_rca"] == "Not enabled" and clear["adverse_media"] == "Not enabled"
                )  # QA-021
                assert cov.unsafe_wording(clear) == []
                again = await open_case(c, tenant, "key-clear-1", person("Alice Wonderland"))
                assert again["request_id"] == clear["request_id"]  # idempotent
                assert (
                    await c.execute(text("SELECT count(*) FROM verify.screening_request_source"))
                ).scalar_one() == 5
                state = (
                    await c.execute(
                        text(
                            "SELECT state FROM verify.screening_request WHERE request_id = CAST(:r AS uuid)"
                        ),
                        {"r": clear["request_id"]},
                    )
                ).scalar_one()
                assert state == "NO_CANDIDATE"

                hit = await open_case(c, tenant, "key-hit-0001", person("Eric Badege"))
                assert hit["finding"] == "Review pending"
                assert (
                    "Badege" not in str(hit)
                    and "UN_SC" not in str(hit)
                    and "candidate" not in hit["finding"].lower()
                )  # QA-040
                detail = await sr.restricted_detail(c, hit["request_id"], now=NOW, max_age=MAX_AGE)
                assert (
                    detail["state"] == "REVIEW_REQUIRED"
                    and detail["subject"]["name"] == "Eric Badege"
                )
                assert detail["candidates"] and detail["candidates"][0]["list_id"] == "UN_SC"
                assert detail["model"]["match_status"] == "POSSIBLE_MATCH"
                assert (
                    detail["model"]["decision"]["status"] == "HOLD_PENDING_REVIEW"
                )  # QA-012: a candidate, never a confirmed match
                assert detail["chain_intact"] is True and detail["decisions"] == []

    asyncio.run(run())


def test_decisions_are_an_append_only_chain_under_maker_and_checker(
    rt_url: str, owner_url: str
) -> None:
    tenant = uid()

    async def run() -> None:
        async with engine(rt_url) as eng:
            await load_lists(eng, at=NOW - timedelta(hours=1))
            async with tenant_transaction(eng, ctx(tenant)) as c:
                hit = await open_case(c, tenant, "key-hit-0002", person("Eric Badege"))
                rid = hit["request_id"]
                clear = proposal(rid, cov.Disposition.CLEARED_FOR_PURPOSE, disp.Action.RELEASE)
                with pytest.raises(ScreeningError) as e:
                    await sr.decide(c, rid, clear, checker=None, checker_role=None, now=NOW)
                assert e.value.code == "CHECKER_REQUIRED"
                with pytest.raises(ScreeningError) as e2:
                    await sr.decide(
                        c, rid, clear, checker="analyst-1", checker_role=disp.Role.SENIOR, now=NOW
                    )
                assert e2.value.code == "CHECKER_IS_THE_MAKER"  # QA-039
                with pytest.raises(ScreeningError) as e3:
                    await sr.decide(
                        c,
                        rid,
                        proposal(
                            rid, cov.Disposition.CLEARED_FOR_PURPOSE, disp.Action.RELEASE, rev=3
                        ),
                        checker="senior-1",
                        checker_role=disp.Role.SENIOR,
                        now=NOW,
                    )
                assert e3.value.code == "REVISION_CONFLICT"  # QA-049
                done = await sr.decide(
                    c, rid, clear, checker="senior-1", checker_role=disp.Role.SENIOR, now=NOW
                )
                assert done["revision"] == 1 and done["state"] == "CLOSED_CLEAR_FOR_SCOPE"
                view = await sr.customer_view(c, rid, now=NOW, max_age=MAX_AGE)
                assert (
                    view["finding"] == "No candidate was found in the checked sources"
                )  # a false positive on the only required class
                detail = await sr.restricted_detail(c, rid, now=NOW, max_age=MAX_AGE)
                assert (
                    detail["model"]["match_status"] == "FALSE_POSITIVE"
                    and detail["model"]["review"] == "DUAL_REVIEWED"
                )
                assert detail["chain_intact"] and len(detail["decisions"]) == 1
                # a later restriction is a new revision citing the first; the first stays
                restrict = proposal(
                    rid,
                    cov.Disposition.RESTRICTED,
                    disp.Action.HOLD,
                    rev=1,
                    rationale="A new alias was found on the list entry that matches the subject.",
                )
                again = await sr.decide(
                    c,
                    rid,
                    restrict,
                    checker="senior-2",
                    checker_role=disp.Role.SENIOR,
                    now=NOW + timedelta(days=1),
                )
                assert again["revision"] == 2 and again["state"] == "CLOSED_RESTRICTED"
                detail = await sr.restricted_detail(
                    c, rid, now=NOW + timedelta(days=1), max_age=MAX_AGE
                )
                assert [d["revision"] for d in detail["decisions"]] == [1, 2] and detail[
                    "chain_intact"
                ]
                assert detail["model"]["decision"]["status"] == "RESTRICTED"
                # a customer administrator cannot decide at all
                with pytest.raises(ScreeningError) as e4:
                    await sr.decide(
                        c,
                        rid,
                        proposal(
                            rid,
                            cov.Disposition.CLEARED_FOR_PURPOSE,
                            disp.Action.RELEASE,
                            maker="c1",
                            role=disp.Role.CUSTOMER_ADMIN,
                            rev=2,
                        ),
                        checker="s9",
                        checker_role=disp.Role.SENIOR,
                        now=NOW,
                    )
                assert e4.value.code == "ROLE_CANNOT_DECIDE"
        async with engine(owner_url) as owner:
            for sql in (
                "UPDATE verify.screening_request_decision SET case_hash = repeat('0', 64)",
                "DELETE FROM verify.screening_request_decision",
            ):
                refused = False
                try:
                    async with owner.begin() as c:
                        await c.execute(text(sql))
                except DBAPIError:
                    refused = True
                assert refused, sql

    asyncio.run(run())


def test_a_stale_or_missing_source_is_unavailable_and_never_clear(rt_url: str) -> None:
    tenant = uid()

    async def run() -> None:
        async with engine(rt_url) as eng:
            await load_lists(
                eng, at=NOW - timedelta(hours=60)
            )  # older than the 48 hours a list may be
            async with tenant_transaction(eng, ctx(tenant)) as c:
                stale = await open_case(c, tenant, "key-stale-001", person("Alice Wonderland"))
                assert (
                    stale["finding"] == "Further information is needed"
                    and stale["coverage"] == "A source needs refreshing"
                )
                detail = await sr.restricted_detail(
                    c, stale["request_id"], now=NOW, max_age=MAX_AGE
                )
                assert detail["state"] == "SOURCE_UNAVAILABLE"
                assert detail["model"]["decision"]["reason_codes"] == ["REQUIRED_SOURCE_STALE"]
        async with engine(rt_url) as eng:
            async with eng.begin():
                pass
            await load_lists(eng, at=NOW - timedelta(hours=1))
            async with tenant_transaction(eng, ctx(tenant)) as c:
                await c.execute(
                    text(
                        "UPDATE verify.sanction_list_version SET active = false WHERE list_id = 'UK_SANCTIONS'"
                    )
                )
                missing = await open_case(c, tenant, "key-missing-01", person("Alice Wonderland"))
                assert (
                    missing["finding"] == "Further information is needed"
                    and missing["coverage"] == "A source is unavailable"
                )
                detail = await sr.restricted_detail(
                    c, missing["request_id"], now=NOW, max_age=MAX_AGE
                )
                assert (
                    detail["state"] == "SOURCE_UNAVAILABLE"
                    and detail["model"]["decision"]["status"] == "UNRESOLVED"
                )
                assert detail["model"]["sanctions"]["result"] is None  # no fabricated clean outcome

    asyncio.run(run())


def test_one_tenant_cannot_read_anothers_case_and_the_reviewer_queue_lists_without_a_name(
    rt_url: str,
) -> None:
    a, b = uid(), uid()

    async def run() -> None:
        async with engine(rt_url) as eng:
            await load_lists(eng, at=NOW - timedelta(hours=1))
            async with tenant_transaction(eng, ctx(a)) as c:
                mine = await open_case(c, a, "key-iso-a-001", person("Eric Badege"))
            async with tenant_transaction(eng, ctx(b)) as c:
                theirs = await open_case(c, b, "key-iso-b-001", person("Eric Badege"))
                with pytest.raises(ScreeningError) as e:
                    await sr.customer_view(c, mine["request_id"], now=NOW, max_age=MAX_AGE)
                assert (
                    e.value.code == "CASE_NOT_FOUND"
                )  # QA-035: tenant A's case does not exist for tenant B
                visible = (
                    await c.execute(text("SELECT count(*) FROM verify.screening_request"))
                ).scalar_one()
                assert visible == 1
            async with tenant_transaction(
                eng, ctx(uid())
            ) as c:  # a reviewer session: the queue reads across tenants
                page = await sr.queue(c, state="REVIEW_REQUIRED", limit=1)
                assert len(page["items"]) == 1 and page["next_cursor"]
                # walk the whole queue page by page: other tests' cases may be waiting too, so the two cases are found by paging, not by position
                seen = list(page["items"])
                cursor = page["next_cursor"]
                for _ in range(200):
                    if not cursor:
                        break
                    more = await sr.queue(
                        c, state="REVIEW_REQUIRED", limit=50, after=_cursor(cursor)
                    )
                    seen += more["items"]
                    cursor = more["next_cursor"]
                ids = {i["request_id"] for i in seen}
                assert {mine["request_id"], theirs["request_id"]} <= ids
                assert all("Badege" not in str(i) and "name" not in i for i in seen)
                # and the session is not left a reviewer's
                assert (
                    await c.execute(text("SELECT count(*) FROM verify.screening_request"))
                ).scalar_one() == 0

    asyncio.run(run())


def _cursor(value: str) -> tuple[datetime, str]:
    at, rid = value.split("|")
    return datetime.fromisoformat(at), rid
