"""The scheduled screening passes on PostgreSQL (ZID-ED-04 section 07): lists fetched and loaded, a failing list changing nothing,
and cases screened again when a list changes."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from conftest import ctx, engine
from sqlalchemy import text
from zid_crypto import KeyRing, LocalSigner
from zid_db import tenant_transaction
from zid_platform.modules.screening.application import passes
from zid_platform.modules.screening.application import screening as service
from zid_platform.modules.screening.domain.decision import (
    AuthorityEvidence,
    AuthorityKind,
    Role,
    ScreeningStatus,
)
from zid_platform.modules.screening.domain.lists import ListId, PartyKind
from zid_platform.modules.screening.domain.matching import Party
from zid_platform.modules.screening.domain.ownership import Graph, Holder, HolderKind
from zid_platform.modules.screening.infrastructure.config import ScreeningConfig, Source, config

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
RING = KeyRing(LocalSigner("screening-passes"))


def sign(body: dict[str, Any]) -> str:
    return RING.sign(body, typ="zid-company-evidence+jwt")


def un(*names: tuple[str, str]) -> bytes:
    people = "".join(
        f"<INDIVIDUAL><DATAID>{i}</DATAID><FIRST_NAME>{a}</FIRST_NAME><SECOND_NAME>{b}</SECOND_NAME></INDIVIDUAL>"
        for i, (a, b) in enumerate(names, start=500)
    )
    return f"<CONSOLIDATED_LIST><INDIVIDUALS>{people}</INDIVIDUALS><ENTITIES/></CONSOLIDATED_LIST>".encode()


SDN = b'1,"DOE, Norman","individual","SDGT",-0- ,-0- ,-0- ,-0- ,-0- ,-0- ,-0- ,"DOB 1950"\n'
UK = b"Unique ID,Name 1,Name 6,Name type\nUK1,Pavel,Nobody,Primary Name\n"
EU = b'<export><sanctionEntity logicalId="1"><subjectType code="person"/><nameAlias wholeName="Quentin Imaginary"/></sanctionEntity></export>'


def source(list_id: ListId, **urls: str) -> Source:
    return Source(list_id, urls, (), "x")


def test_the_ingest_pass_loads_what_has_an_address_and_one_failure_changes_nothing(
    rt_url: str,
) -> None:
    bodies = {
        "ofac.example.gov": {"/SDN.CSV": SDN, "/CONS.CSV": SDN},
        "uk.example.gov": {"/uk.csv": UK},
        "eu.example.eu": {"/eu.xml": EU},
        "un.example.org": {"/un.xml": un(("Eric", "Badege"), ("Aaa", "Bbb"))},
    }
    broken = {"eu.example.eu"}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host in broken:
            return httpx.Response(503)
        return httpx.Response(200, content=bodies[request.url.host][request.url.path])

    cfg = ScreeningConfig(
        config().policy,
        config().packs,
        {
            ListId.OFAC_SDN: source(ListId.OFAC_SDN, sdn="https://ofac.example.gov/SDN.CSV"),
            ListId.OFAC_NON_SDN: source(
                ListId.OFAC_NON_SDN, sdn="https://ofac.example.gov/CONS.CSV"
            ),
            ListId.UK_SANCTIONS: source(ListId.UK_SANCTIONS, file="https://uk.example.gov/uk.csv"),
            ListId.EU_CONSOLIDATED: source(
                ListId.EU_CONSOLIDATED, file="https://eu.example.eu/eu.xml"
            ),
            ListId.UN_SC: source(ListId.UN_SC, file="https://un.example.org/un.xml"),
            ListId.LOCAL: Source(ListId.LOCAL, {}, (), "csv"),
        },
    )

    async def run() -> None:
        async with engine(rt_url) as eng:
            async with eng.begin() as c:
                first = await passes.ingest_pass(
                    c, cfg, NOW, transport=httpx.MockTransport(handler)
                )
            assert first == {
                "OFAC_SDN": "LOADED", "OFAC_NON_SDN": "LOADED", "UK_SANCTIONS": "LOADED",
                "EU_CONSOLIDATED": "FETCH_FAILED",  # the EU source is down: the others still loaded
                "UN_SC": "LOADED", "LOCAL": "NO_ADDRESS",
            }  # fmt: skip
            async with eng.begin() as c:
                again = await passes.ingest_pass(
                    c, cfg, NOW + timedelta(hours=6), transport=httpx.MockTransport(handler)
                )
            assert again["UN_SC"] == "UNCHANGED"  # the same file only refreshes its fetch time
            bodies["un.example.org"]["/un.xml"] = b"<html>maintenance</html>"
            async with eng.begin() as c:
                bad = await passes.ingest_pass(
                    c, cfg, NOW + timedelta(hours=7), transport=httpx.MockTransport(handler)
                )
                assert bad["UN_SC"] == "LIST_FORMAT_INVALID" and bad["UK_SANCTIONS"] == "UNCHANGED"
                row = (
                    await c.execute(
                        text(
                            "SELECT entry_count, fetched_at FROM verify.sanction_list_version WHERE list_id = 'UN_SC' AND active"
                        )
                    )
                ).one()
                assert row.entry_count == 2 and row.fetched_at == NOW + timedelta(
                    hours=6
                )  # the list in use is untouched

    asyncio.run(run())


def test_the_rescreen_pass_screens_again_what_a_list_change_made_stale(
    rt_url: str, owner_url: str
) -> None:
    tenant, requester = str(uuid.uuid4()), str(uuid.uuid4())
    cfg = config()
    graph = Graph("co", {"co": Holder("co", HolderKind.ENTITY)}, ())
    subjects = [
        (Role.ENTITY, Party("co", PartyKind.ENTITY, "Acme Widgets Limited")),
        (Role.REPRESENTATIVE, Party("rep", PartyKind.PERSON, "Alice Smith")),
    ]
    gathered = AuthorityEvidence(AuthorityKind.REGISTER_OFFICER, "officer-1")

    async def lists(eng: Any, un_bytes: bytes, at: datetime) -> None:
        async with eng.begin() as c:
            for lid, files in (
                (ListId.OFAC_SDN, {"sdn": SDN}),
                (ListId.OFAC_NON_SDN, {"sdn": SDN}),
                (ListId.UK_SANCTIONS, {"file": UK}),
                (ListId.EU_CONSOLIDATED, {"file": EU}),
                (ListId.UN_SC, {"file": un_bytes}),
            ):
                await service.ingest_list(c, lid, files, fetched_at=at, source_ref="t", now=at)

    async def run() -> None:
        async with engine(rt_url) as eng, engine(owner_url) as worker:
            await lists(eng, un(("Eric", "Badege"), ("Aaa", "Bbb"), ("Ccc", "Ddd")), NOW)
            async with tenant_transaction(eng, ctx(tenant)) as c:
                v = await service.open_case(
                    c, tenant, country="GB", subjects=subjects, graph=graph, authority=gathered,
                    requested_by=requester, idempotency_key="pass-0000001", cfg=cfg, sign=sign, now=NOW,
                )  # fmt: skip
                # a one-person structure with no declared owners is a gap, so this case is in review, not clear
                assert v.status in (ScreeningStatus.REVIEW_REQUIRED, ScreeningStatus.VERIFIED)
                case_id = v.case_id
            async with worker.begin() as c:  # the worker role sees every tenant
                assert (await passes.rescreen_pass(c, cfg, sign, NOW + timedelta(minutes=5))).get(
                    tenant
                ) == 0  # nothing changed
            await lists(
                eng, un(("Eric", "Badege"), ("Aaa", "Bbb"), ("Ccc", "Ddd"), ("Alice", "Smith")), NOW
            )
            async with worker.begin() as c:
                done = await passes.rescreen_pass(c, cfg, sign, NOW + timedelta(minutes=10))
            assert done[tenant] == 1  # the new designation made the case due
            async with tenant_transaction(eng, ctx(tenant)) as c:
                rows = await service.history(c, tenant, case_id)
                assert [r.version for r in rows] == [1, 2] and "SCREENING_ALERT" in rows[1].reasons
                assert (
                    rows[1].decided_by is None
                )  # the system screened it again; no person decided anything
            async with worker.begin() as c:
                assert (await passes.rescreen_pass(c, cfg, sign, NOW + timedelta(minutes=15))).get(
                    tenant
                ) == 0  # and only once

    asyncio.run(run())


def test_the_rescreen_pass_holds_a_case_whose_country_the_registry_has_closed(
    rt_url: str, owner_url: str
) -> None:
    """ZID-EDR-001: country permissions hold in jobs. A closed country is left alone (no screening, no appended decision), reported
    as held, and looked at again once it is open."""
    tenant, requester = str(uuid.uuid4()), str(uuid.uuid4())
    cfg = config()
    graph = Graph("co", {"co": Holder("co", HolderKind.ENTITY)}, ())
    subjects = [
        (Role.ENTITY, Party("co", PartyKind.ENTITY, "Acme Widgets Limited")),
        (Role.REPRESENTATIVE, Party("rep", PartyKind.PERSON, "Alice Smith")),
    ]
    gathered = AuthorityEvidence(AuthorityKind.REGISTER_OFFICER, "officer-1")

    def closed(country: str) -> None:
        raise ValueError(f"business verification is not open for {country}")

    async def lists(eng: Any, un_bytes: bytes) -> None:
        async with eng.begin() as c:
            for lid, files in (
                (ListId.OFAC_SDN, {"sdn": SDN}), (ListId.OFAC_NON_SDN, {"sdn": SDN}), (ListId.UK_SANCTIONS, {"file": UK}),
                (ListId.EU_CONSOLIDATED, {"file": EU}), (ListId.UN_SC, {"file": un_bytes}),
            ):  # fmt: skip
                await service.ingest_list(c, lid, files, fetched_at=NOW, source_ref="t", now=NOW)

    async def run() -> None:
        async with engine(rt_url) as eng, engine(owner_url) as worker:
            await lists(eng, un(("Eric", "Badege"), ("Aaa", "Bbb"), ("Ccc", "Ddd")))
            async with tenant_transaction(eng, ctx(tenant)) as c:
                v = await service.open_case(
                    c, tenant, country="GB", subjects=subjects, graph=graph, authority=gathered,
                    requested_by=requester, idempotency_key="gate-0000001", cfg=cfg, sign=sign, now=NOW,
                )  # fmt: skip
                case_id = v.case_id
            await lists(
                eng, un(("Eric", "Badege"), ("Aaa", "Bbb"), ("Ccc", "Ddd"), ("Alice", "Smith"))
            )  # the case is now due
            async with worker.begin() as c:
                held = await passes.rescreen_pass(
                    c, cfg, sign, NOW + timedelta(minutes=10), country_gate=closed
                )
            assert held[tenant] == 0 and held[f"{tenant}:held_by_country"] == 1
            async with tenant_transaction(eng, ctx(tenant)) as c:
                assert [r.version for r in await service.history(c, tenant, case_id)] == [
                    1
                ]  # nothing was screened or appended
            async with worker.begin() as c:
                done = await passes.rescreen_pass(
                    c, cfg, sign, NOW + timedelta(minutes=20), country_gate=lambda _c: None
                )
            assert (
                done[tenant] == 1 and f"{tenant}:held_by_country" not in done
            )  # open again: screened as usual
            async with tenant_transaction(eng, ctx(tenant)) as c:
                assert [r.version for r in await service.history(c, tenant, case_id)] == [1, 2]

    asyncio.run(run())
