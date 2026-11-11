"""Screening on PostgreSQL (ZID-ED-04): lists loaded whole or not at all, signed append-only evidence, restricted records kept
out of every safe surface, alerts that wait for a person, rescreening when a list changes, and tenant isolation."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from conftest import ctx, engine
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from zid_crypto import KeyRing, LocalSigner, verify_jws
from zid_db import tenant_transaction
from zid_errors import ZidError
from zid_platform.modules.screening.application import screening as service
from zid_platform.modules.screening.domain.decision import (
    AuthorityEvidence,
    AuthorityKind,
    Role,
    Ruling,
    ScreeningError,
    ScreeningStatus,
)
from zid_platform.modules.screening.domain.lists import ListId, PartyKind
from zid_platform.modules.screening.domain.matching import Party
from zid_platform.modules.screening.domain.ownership import Edge, Graph, Holder, HolderKind
from zid_platform.modules.screening.infrastructure import store
from zid_platform.modules.screening.infrastructure.config import config

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
TYP = "zid-screening-evidence+jwt"
SIGNER = LocalSigner("screening-test")
RING = KeyRing(SIGNER)
CFG = config()


def uid() -> str:
    return str(uuid.uuid4())


def sign(body: dict[str, Any]) -> str:
    return RING.sign(body, typ=TYP)


def un_xml(*names: tuple[str, str]) -> bytes:
    people = "".join(
        f"<INDIVIDUAL><DATAID>{i}</DATAID><FIRST_NAME>{first}</FIRST_NAME><SECOND_NAME>{last}</SECOND_NAME>"
        f"<UN_LIST_TYPE>T</UN_LIST_TYPE></INDIVIDUAL>"
        for i, (first, last) in enumerate(names, start=900)
    )
    return f"<CONSOLIDATED_LIST><INDIVIDUALS>{people}</INDIVIDUALS><ENTITIES/></CONSOLIDATED_LIST>".encode()


SDN = b'1,"DOE, Norman","individual","SDGT",-0- ,-0- ,-0- ,-0- ,-0- ,-0- ,-0- ,"DOB 1950"\n2,"ZZZ HOLDING","-0-","SDGT",-0- ,-0- ,-0- ,-0- ,-0- ,-0- ,-0- ,-0- \n'
UK = b"Unique ID,Name 1,Name 6,Name type\nUK1,Pavel,Nobody,Primary Name\nUK2,Karl,Fictional,Primary Name\n"
EU = b'<export><sanctionEntity logicalId="1"><subjectType code="person"/><nameAlias wholeName="Quentin Imaginary"/></sanctionEntity></export>'


async def load_lists(eng: Any, *, un: bytes | None = None, at: datetime = NOW) -> None:
    async with eng.begin() as c:
        await service.ingest_list(
            c, ListId.OFAC_SDN, {"sdn": SDN}, fetched_at=at, source_ref="t", now=at
        )
        await service.ingest_list(
            c, ListId.OFAC_NON_SDN, {"sdn": SDN}, fetched_at=at, source_ref="t", now=at
        )
        await service.ingest_list(
            c, ListId.UK_SANCTIONS, {"file": UK}, fetched_at=at, source_ref="t", now=at
        )
        await service.ingest_list(
            c, ListId.EU_CONSOLIDATED, {"file": EU}, fetched_at=at, source_ref="t", now=at
        )
        await service.ingest_list(
            c,
            ListId.UN_SC,
            {"file": un or un_xml(("Eric", "Badege"), ("Aaa", "Bbb"))},
            fetched_at=at,
            source_ref="t",
            now=at,
        )


def subjects(
    *, owner_name: str = "Olivia Owner", rep: str = "Alice Smith"
) -> list[tuple[Role, Party]]:
    return [
        (Role.ENTITY, Party("co", PartyKind.ENTITY, "Acme Widgets Limited")),
        (Role.REPRESENTATIVE, Party("rep", PartyKind.PERSON, rep)),
        (Role.OWNER, Party("p-owner", PartyKind.PERSON, owner_name)),
    ]


GRAPH = Graph(
    "co",
    {"co": Holder("co", HolderKind.ENTITY), "p-owner": Holder("p-owner", HolderKind.PERSON)},
    (Edge("p-owner", "co", Decimal(60)),),
)
OFFICER = AuthorityEvidence(AuthorityKind.REGISTER_OFFICER, "officer-entry-9")


def test_lists_cases_alerts_review_rescreening_isolation_and_idempotency(rt_url: str) -> None:
    tenant, other_tenant, requester, reviewer = uid(), uid(), uid(), uid()
    # the lists are global reference data and the test database is shared: start from a known active version
    marker = f"{uuid.uuid4().hex[:6]}"

    async def run() -> None:
        async with engine(rt_url) as eng:
            # --- lists: loaded whole or not at all ---------------------------------------------------------------
            await load_lists(eng, un=un_xml(("Eric", "Badege"), ("Aaa", "Bbb"), ("Ccc", marker)))
            async with eng.begin() as c:
                same = await service.ingest_list(
                    c, ListId.UN_SC, {"file": un_xml(("Eric", "Badege"), ("Aaa", "Bbb"), ("Ccc", marker))},
                    fetched_at=NOW + timedelta(hours=5), source_ref="t", now=NOW,
                )  # fmt: skip
                assert same == "UNCHANGED"  # the same file again only keeps the list fresh
                snaps = await store.snapshots(c)
                assert snaps[ListId.UN_SC].fetched_at == NOW + timedelta(hours=5)
                with pytest.raises(ScreeningError, match="LIST_FORMAT_INVALID"):
                    await service.ingest_list(
                        c,
                        ListId.UN_SC,
                        {"file": b"<html>Service unavailable</html>"},
                        fetched_at=NOW,
                        source_ref="t",
                        now=NOW,
                    )
                with pytest.raises(ScreeningError, match="LIST_SHRUNK"):
                    await service.ingest_list(
                        c,
                        ListId.UN_SC,
                        {"file": un_xml(("Only", "One"))},
                        fetched_at=NOW,
                        source_ref="t",
                        now=NOW,
                    )  # one entry where three were in use: a truncated file is refused whole
                assert len(snaps[ListId.UN_SC].entries) == 3  # and the list in use is untouched

            # --- a clean case: verified, signed, expiring, audited, and free of names ------------------------------
            async with tenant_transaction(eng, ctx(tenant)) as c:
                v = await service.open_case(
                    c, tenant, country="GB", subjects=subjects(), graph=GRAPH, authority=OFFICER,
                    requested_by=requester, idempotency_key="scr-00000001", cfg=CFG, sign=sign, now=NOW,
                )  # fmt: skip
                assert (
                    v.status is ScreeningStatus.VERIFIED
                    and v.version == 1
                    and v.expiry_at == NOW + timedelta(days=365)
                )
                rows = await service.history(c, tenant, v.case_id)
                _, claims = verify_jws(rows[0].jws, keys=[SIGNER.public_jwk()], typ=TYP)
                assert claims["evidence_hash"] == rows[0].evidence_hash
                assert {x["list_id"] for x in claims["lists"]} == {
                    lid.value for lid in ListId if lid not in (ListId.LOCAL, ListId.PEP)
                }
                blob = repr(claims) + repr(rows[0].body)
                for private in ("Alice", "Olivia", "Smith", "Acme"):
                    assert private not in blob
                audit = (
                    await c.execute(
                        text(
                            "SELECT body::text AS b FROM evidence.audit_record WHERE tenant_id = :t AND event_type = 'verification.screening.decided'"
                        ),
                        {"t": uuid.UUID(tenant)},
                    )
                ).all()
                assert len(audit) == 1 and all(
                    p not in audit[0].b for p in ("Alice", "Olivia", "Acme")
                )
                clean_case = v.case_id

            # --- the same key is the same case, even under a retry storm ------------------------------------------
            async def open_again() -> str:
                async with tenant_transaction(eng, ctx(tenant)) as c:
                    return (
                        await service.open_case(
                            c, tenant, country="GB", subjects=subjects(), graph=GRAPH, authority=OFFICER,
                            requested_by=requester, idempotency_key="scr-00000001", cfg=CFG, sign=sign, now=NOW,
                        )
                    ).case_id  # fmt: skip

            assert set(await asyncio.gather(*(open_again() for _ in range(3)))) == {clean_case}

            async def race() -> str:
                async with tenant_transaction(eng, ctx(tenant)) as c:
                    return (
                        await service.open_case(
                            c, tenant, country="GB", subjects=subjects(), graph=GRAPH, authority=OFFICER,
                            requested_by=requester, idempotency_key="scr-race-0001", cfg=CFG, sign=sign, now=NOW,
                        )
                    ).case_id  # fmt: skip

            assert len(set(await asyncio.gather(*(race() for _ in range(4))))) == 1

            # --- an alert waits for a person; the customer sees no list detail ------------------------------------
            async with tenant_transaction(eng, ctx(tenant)) as c:
                alerted = await service.open_case(
                    c, tenant, country="GB", subjects=subjects(rep="Eric Badege"), graph=GRAPH, authority=OFFICER,
                    requested_by=requester, idempotency_key="scr-00000002", cfg=CFG, sign=sign, now=NOW,
                )  # fmt: skip
                assert alerted.status is ScreeningStatus.REVIEW_REQUIRED
                assert "Badege" not in repr(alerted) and "UN" not in alerted.next_action
                detail = await service.restricted_detail(c, tenant, alerted.case_id)
                assert (
                    detail["alerts"][0]["matched_name"] == "Eric Badege"
                    and detail["alerts"][0]["list_id"] == "UN_SC"
                )
                assert "SCREENING_ALERT" in detail["reasons"]

            # --- a reviewer rules, independently, with a rationale; history is kept ----------------------------------
            async def rule(
                who: str, rationale: str, outcome: ScreeningStatus = ScreeningStatus.VERIFIED
            ) -> Any:
                async with tenant_transaction(eng, ctx(tenant)) as c:
                    return await service.review(
                        c,
                        tenant,
                        alerted.case_id,
                        ruling=Ruling(who, outcome, rationale),
                        cfg=CFG,
                        sign=sign,
                        now=NOW,
                    )

            with pytest.raises(ScreeningError, match="REVIEWER_CONFLICT"):
                await rule(requester, "Cleared by the person who asked, which is not allowed.")
            with pytest.raises(ScreeningError, match="REVIEWER_CONFLICT"):
                await rule("rep", "Cleared by the screened person, which is not allowed at all.")
            with pytest.raises(ScreeningError, match="RATIONALE_REQUIRED"):
                await rule(reviewer, "fine")
            ruled = await rule(
                reviewer, "Namesake: passport shows another birth year and country of origin."
            )
            assert ruled.status is ScreeningStatus.VERIFIED and ruled.version == 2
            async with tenant_transaction(eng, ctx(tenant)) as c:
                rows = await service.history(c, tenant, alerted.case_id)
                assert [r.decision_status for r in rows] == [
                    ScreeningStatus.REVIEW_REQUIRED,
                    ScreeningStatus.VERIFIED,
                ]
                assert rows[1].decided_by == reviewer and "Namesake" in (rows[1].rationale or "")
                assert (
                    rows[1].body["reviewed"] is True
                    and rows[1].body["matches"] == rows[0].body["matches"]
                )
            with pytest.raises(ScreeningError, match="CASE_NOT_IN_REVIEW"):
                await rule(reviewer, "A second ruling on a case that is no longer in review.")

            # --- evidence and alerts cannot be edited or deleted ------------------------------------------------------
            for statement in (
                "UPDATE verify.screening_evidence SET rationale = 'x'",
                "DELETE FROM verify.screening_evidence",
                "UPDATE verify.screening_match SET score = 0",
                "DELETE FROM verify.screening_match",
            ):
                async with tenant_transaction(eng, ctx(tenant)) as c:
                    with pytest.raises(DBAPIError, match="append-only|permission denied"):
                        await c.execute(text(statement))

            # --- another company sees nothing ---------------------------------------------------------------------------
            async with tenant_transaction(eng, ctx(other_tenant)) as c:
                with pytest.raises(ScreeningError, match="CASE_NOT_FOUND"):
                    await service.get_case(c, other_tenant, alerted.case_id, now=NOW)
                for table in (
                    "screening_party",
                    "screening_match",
                    "screening_evidence",
                    "screening_ownership",
                ):
                    assert (
                        await c.execute(text(f"SELECT count(*) FROM verify.{table}"))
                    ).scalar_one() == 0

            # --- a new designation: the clean case is due, and screening again finds it ---------------------------------
            async with tenant_transaction(eng, ctx(tenant)) as c:
                assert clean_case not in await service.due_for_rescreen(c, tenant, now=NOW)
            await load_lists(
                eng,
                un=un_xml(("Eric", "Badege"), ("Aaa", "Bbb"), ("Ccc", marker), ("Alice", "Smith")),
            )
            async with tenant_transaction(eng, ctx(tenant)) as c:
                assert clean_case in await service.due_for_rescreen(c, tenant, now=NOW)
                again = await service.rescreen(c, tenant, clean_case, cfg=CFG, sign=sign, now=NOW)
                assert again.status is ScreeningStatus.REVIEW_REQUIRED and again.version == 2
                rows = await service.history(c, tenant, clean_case)
                assert [r.decision_status for r in rows] == [
                    ScreeningStatus.VERIFIED,
                    ScreeningStatus.REVIEW_REQUIRED,
                ]
                assert (
                    rows[0].body["lists"] != rows[1].body["lists"]
                )  # the earlier clearance still names the old list version

            # --- a list that goes stale means not screened, never clear ---------------------------------------------------
            await load_lists(
                eng,
                un=un_xml(("Eric", "Badege"), ("Aaa", "Bbb"), ("Ccc", marker), ("Alice", "Smith")),
                at=NOW,
            )
            async with tenant_transaction(eng, ctx(tenant)) as c:
                stale = await service.rescreen(
                    c, tenant, clean_case, cfg=CFG, sign=sign, now=NOW + timedelta(hours=49)
                )
                assert stale.status is ScreeningStatus.UNAVAILABLE

            # --- a country that is not open creates no case ----------------------------------------------------------------
            def closed(country: str) -> None:
                raise ZidError("COUNTRY_CAPABILITY_UNAVAILABLE", country, http_status=422)

            async with tenant_transaction(eng, ctx(tenant)) as c:
                with pytest.raises(ZidError):
                    await service.open_case(
                        c, tenant, country="GB", subjects=subjects(), graph=GRAPH, authority=OFFICER,
                        requested_by=requester, idempotency_key="scr-closed-001", cfg=CFG, sign=sign, now=NOW,
                        country_gate=closed,
                    )  # fmt: skip
                assert (
                    await c.execute(
                        text(
                            "SELECT count(*) FROM verify.screening_case WHERE idempotency_key = 'scr-closed-001'"
                        )
                    )
                ).scalar_one() == 0

    asyncio.run(run())
