"""The market matrix on Postgres: maker-checker, four independent roles, immutability, and the wiring into the configuration check (ZID-COM-WF-2026-11 WF-I01, WF-I05; migration commercial_0027).

A matrix is activated only by four different people, none the author; once active it cannot be edited; and only an ACTIVE matrix makes a family `sellable` in
POST /v1/commercial/configurations:check. Rows and approvals are references to signed evidence: nothing here can make a reference true.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from conftest import engine
from fastapi.testclient import TestClient
from onboarding_fixture import ISS, onboard
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_commercial_pg import settings
from zid_platform.main import create_app
from zid_platform.modules.commercial.ports import market_matrix_model as mm
from zid_platform.modules.commercial.ports import market_matrix_store as store

URL = "/v1/commercial/configurations:check"


def uid() -> str:
    return str(uuid.uuid4())


def full_row(family: str = "API", country: str = "DE", region: str = "eu-central") -> mm.Row:
    return mm.Row(
        family=family, country=country, seller_entity="Zoiko Tech GmbH", merchant_route="stripe-eu", currency="EUR",
        tax_class="B2B-SERVICES", data_region=region, offer_ref="offer:2026-11/api-de", legal_basis_ref="legal:memo-114",
        release_evidence_ref="release:G6", payment_configured=True, tax_configured=True,
    )  # fmt: skip


async def signed(conn, matrix_id: str, author: str) -> list[str]:
    people = [uid() for _ in mm.ROLES]
    for role, who in zip(mm.ROLES, people, strict=True):
        await store.approve(conn, matrix_id, role=role, approver=who)
    return people


async def refused(conn, call, code: str) -> None:
    with pytest.raises(Exception) as e:
        async with conn.begin_nested():
            await call()
    assert getattr(e.value, "code", None) == code, repr(e.value)


def test_four_independent_roles_activate_and_an_active_matrix_never_changes(rt_url: str) -> None:
    async def run() -> None:
        author, activator = uid(), uid()
        async with engine(rt_url) as eng, eng.begin() as c:
            d = await store.create_draft(c, copy_of=None, created_by=author, note="nov 2026")
            mid = d["matrix_id"]
            await store.put_row(c, mid, full_row())

            # the author cannot sign, nor activate
            await refused(
                c,
                lambda: store.approve(c, mid, role="PRODUCT", approver=author),
                "MARKET_MATRIX_SELF_APPROVAL",
            )
            # an unknown role is refused
            await refused(
                c,
                lambda: store.approve(c, mid, role="CEO", approver=uid()),
                "MARKET_MATRIX_ROLE_UNKNOWN",
            )

            # one person cannot sign for two roles; a role cannot be signed twice
            p1 = uid()
            await store.approve(c, mid, role="PRODUCT", approver=p1)
            await refused(
                c,
                lambda: store.approve(c, mid, role="FINANCE", approver=p1),
                "MARKET_MATRIX_PERSON_ALREADY_SIGNED",
            )
            await refused(
                c,
                lambda: store.approve(c, mid, role="PRODUCT", approver=uid()),
                "MARKET_MATRIX_ROLE_ALREADY_SIGNED",
            )

            # three of four is not enough
            for role in ("FINANCE", "LEGAL_PRIVACY"):
                await store.approve(c, mid, role=role, approver=uid())
            now = datetime.now(UTC)
            await refused(
                c,
                lambda: store.activate(c, mid, activator=activator, effective_from=now, now=now),
                "MARKET_MATRIX_APPROVALS_MISSING",
            )
            await store.approve(c, mid, role="SECURITY_QA", approver=uid())
            await refused(
                c,
                lambda: store.activate(c, mid, activator=author, effective_from=now, now=now),
                "MARKET_MATRIX_SELF_APPROVAL",
            )
            assert (await store.active(c, now + timedelta(seconds=1))) is None

            await store.activate(c, mid, activator=activator, effective_from=now, now=now)
            m, rows = await store.active_rows(c, now + timedelta(seconds=1))
            assert m is not None and str(m["matrix_id"]) == mid and list(rows) == [("API", "DE")]

            # the database, not only the code, refuses to change an active matrix, its rows or its approvals
            for sql in (
                "UPDATE commercial.market_matrix SET note = 'edited' WHERE matrix_id = :m",
                "DELETE FROM commercial.market_matrix WHERE matrix_id = :m",
                "UPDATE commercial.market_matrix_row SET seller_entity = 'Someone Else' WHERE matrix_id = :m",
                "DELETE FROM commercial.market_matrix_row WHERE matrix_id = :m",
                "DELETE FROM commercial.market_matrix_approval WHERE matrix_id = :m",
                "UPDATE commercial.market_matrix_approval SET approver = gen_random_uuid() WHERE matrix_id = :m",
            ):
                with pytest.raises(DBAPIError):
                    async with c.begin_nested():
                        await c.execute(text(sql), {"m": uuid.UUID(mid)})
            await refused(
                c, lambda: store.put_row(c, mid, full_row(country="FR")), "MARKET_MATRIX_NOT_DRAFT"
            )
            await refused(c, lambda: store.withdraw(c, mid), "MARKET_MATRIX_NOT_DRAFT")

            # a next version is a copy, signed afresh, and supersedes the first only when activated
            nxt = (await store.create_draft(c, copy_of=mid, created_by=author, note=None))[
                "matrix_id"
            ]
            assert [r.country for r in await store.rows(c, nxt)] == ["DE"]
            assert await store.approvals(c, nxt) == [], "a copy carries no approvals"
            await store.put_row(c, nxt, full_row(country="FR"))
            await signed(c, nxt, author)
            later = now + timedelta(minutes=1)
            await store.activate(c, nxt, activator=activator, effective_from=later, now=later)
            old = await store.matrix(c, mid)
            assert old is not None and old["status"] == "SUPERSEDED"
            _, rows2 = await store.active_rows(c, later + timedelta(seconds=1))
            assert set(rows2) == {("API", "DE"), ("API", "FR")}

    asyncio.run(run())


def test_an_empty_matrix_and_a_withdrawn_draft_cannot_be_activated(rt_url: str) -> None:
    async def run() -> None:
        author, activator = uid(), uid()
        async with engine(rt_url) as eng, eng.begin() as c:
            mid = (await store.create_draft(c, copy_of=None, created_by=author, note=None))[
                "matrix_id"
            ]
            await signed(c, mid, author)
            now = datetime.now(UTC)
            await refused(
                c,
                lambda: store.activate(c, mid, activator=activator, effective_from=now, now=now),
                "MARKET_MATRIX_EMPTY",
            )
            await store.withdraw(c, mid)
            await refused(
                c,
                lambda: store.activate(c, mid, activator=activator, effective_from=now, now=now),
                "MARKET_MATRIX_NOT_DRAFT",
            )
            await refused(
                c,
                lambda: store.approve(c, mid, role="PRODUCT", approver=uid()),
                "MARKET_MATRIX_NOT_DRAFT",
            )
            await refused(
                c,
                lambda: store.create_draft(c, copy_of=uid(), created_by=author, note=None),
                "MARKET_MATRIX_NOT_FOUND",
            )

    asyncio.run(run())


def test_only_an_active_approved_matrix_makes_a_family_sellable_in_the_check(rt_url: str) -> None:
    o = onboard(rt_url, extra_actions=["commercial.read"])
    app = create_app(settings(rt_url), keyring=o.ring)
    adm = o.bearer(o.owner)
    body = {
        "families": ["API"],
        "subject_country": "DE",
        "payer_country": "DE",
        "operating_region": "eu-central",
    }

    async def activate_matrix() -> str:
        author, activator = uid(), uid()
        async with engine(rt_url) as eng, eng.begin() as c:
            mid = (await store.create_draft(c, copy_of=None, created_by=author, note="wiring"))[
                "matrix_id"
            ]
            await store.put_row(c, mid, full_row())
            await signed(c, mid, author)
            now = datetime.now(UTC) - timedelta(seconds=5)
            await store.activate(c, mid, activator=activator, effective_from=now, now=now)
            return mid

    with TestClient(app, base_url=ISS) as http:
        # a draft, however complete, changes nothing: before activation nothing is sellable
        before = http.post(URL, headers=adm, json=body).json()
        assert before["families"][0]["sellable"] is False
        assert before.get("market_matrix_version") is None

        mid = asyncio.run(activate_matrix())
        after = http.post(URL, headers=adm, json=body).json()
        fam = after["families"][0]
        assert fam["sellable"] is True and fam["refusals"] == [], after
        assert after["market_matrix_version"] is not None
        assert after["estimate"] is None and "$" not in str(after), "still no invented price"

        # a country the matrix has no row for stays a refusal, and so does a family it does not list
        fr = http.post(URL, headers=adm, json={**body, "payer_country": "FR"}).json()["families"][0]
        assert fr["sellable"] is False and {r["dimension"] for r in fr["refusals"]} == {"payer"}
        other = http.post(URL, headers=adm, json={**body, "families": ["IDV"]}).json()["families"][
            0
        ]
        assert other["sellable"] is False
        assert mid
