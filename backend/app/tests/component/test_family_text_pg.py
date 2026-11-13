"""Family wording revisions on Postgres (ZID-COM-WF-2026-11 WF-I01, G01): maker-checker, immutable, shown to buyers through the public catalogue and the offers, and never changing
what a family needs."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

import pytest
from conftest import engine
from fastapi.testclient import TestClient
from onboarding_fixture import ISS, onboard
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_commercial_pg import settings
from zid_platform.main import create_app
from zid_platform.modules.commercial.ports import family_text_store as store


def uid() -> str:
    return str(uuid.uuid4())


async def refused(conn, call, code: str) -> None:
    with pytest.raises(Exception) as e:
        async with conn.begin_nested():
            await call()
    assert getattr(e.value, "code", None) == code, repr(e.value)


def test_wording_is_maker_checker_immutable_and_reaches_the_public_catalogue(
    rt_url: str, owner_url: str
) -> None:
    o = onboard(rt_url, extra_actions=["commercial.read"])
    app = create_app(settings(rt_url), keyring=o.ring)
    author, checker = uid(), uid()
    with TestClient(app, base_url=ISS) as http:
        before = {f["family"]: f for f in http.get("/v1/catalog/families").json()["families"]}
        base_name = before["DOC"]["name"]

        async def lifecycle() -> str:
            async with engine(rt_url) as eng, eng.begin() as c:
                await refused(
                    c,
                    lambda: store.create_draft(
                        c,
                        family="GHOST",
                        name="Ghost",
                        unit="Unit",
                        sales_qualifier="",
                        reason="test",
                        created_by=author,
                    ),
                    "FAMILY_UNKNOWN",
                )
                await refused(
                    c,
                    lambda: store.create_draft(
                        c,
                        family="DOC",
                        name="Documents from $1",
                        unit="Check",
                        sales_qualifier="",
                        reason="test",
                        created_by=author,
                    ),
                    "PRICE_IN_TEXT",
                )
                await refused(
                    c,
                    lambda: store.create_draft(
                        c,
                        family="DOC",
                        name="Certified documents",
                        unit="Check",
                        sales_qualifier="",
                        reason="test",
                        created_by=author,
                    ),
                    "CLAIM_IN_LABEL",
                )
                rev = (
                    await store.create_draft(
                        c,
                        family="DOC",
                        name="Document checks",
                        unit="Document examined",
                        sales_qualifier="OCR, tamper checks and source trace",
                        reason="clearer wording",
                        created_by=author,
                    )
                )["rev_id"]
                await refused(
                    c,
                    lambda: store.activate(c, rev, activator=author, now=datetime.now(UTC)),
                    "FAMILY_TEXT_SELF_ACTIVATION",
                )
                return rev

        rev = asyncio.run(lifecycle())
        # a draft changes nothing a buyer sees
        assert {f["family"]: f for f in http.get("/v1/catalog/families").json()["families"]}["DOC"][
            "name"
        ] == base_name

        async def activate() -> None:
            async with engine(rt_url) as eng, eng.begin() as c:
                await store.activate(c, rev, activator=checker, now=datetime.now(UTC))
                await refused(
                    c,
                    lambda: store.activate(c, rev, activator=checker, now=datetime.now(UTC)),
                    "FAMILY_TEXT_NOT_DRAFT",
                )
                await refused(c, lambda: store.withdraw(c, rev), "FAMILY_TEXT_NOT_DRAFT")

        asyncio.run(activate())
        after = {f["family"]: f for f in http.get("/v1/catalog/families").json()["families"]}
        assert (
            after["DOC"]["name"] == "Document checks"
            and after["DOC"]["unit"] == "Document examined"
        )
        # the wording changed; what the family needs and is did not
        for key in ("depends_on", "regulated", "sellable", "next_action", "causes"):
            assert after["DOC"][key] == before["DOC"][key], key
        assert after["SIGN"] == before["SIGN"], "another family is untouched"
        # the offers show the same wording
        cards = {
            c["family"]: c
            for c in http.get("/v1/commercial/offers", headers=o.bearer(o.owner)).json()["cards"]
        }
        assert cards["DOC"]["name"] == "Document checks"

        # a newer revision supersedes it; the older stays as evidence and nothing is ever edited or deleted
        async def second() -> None:
            async with engine(rt_url) as eng, eng.begin() as c:
                nxt = (
                    await store.create_draft(
                        c,
                        family="DOC",
                        name="Document authenticity",
                        unit="Document examined",
                        sales_qualifier="",
                        reason="align with the design document",
                        created_by=author,
                    )
                )["rev_id"]
                await store.activate(c, nxt, activator=checker, now=datetime.now(UTC))
                revs = await store.revisions(c, "DOC")
                assert [(r["version"], r["status"]) for r in revs] == [
                    (2, "ACTIVE"),
                    (1, "SUPERSEDED"),
                ]

        asyncio.run(second())
        assert {f["family"]: f for f in http.get("/v1/catalog/families").json()["families"]}["DOC"][
            "name"
        ] == "Document authenticity"

    async def immutable() -> None:
        async with engine(owner_url) as eng:
            for sql in (
                "UPDATE commercial.service_family_text SET name = 'Edited in place'",
                "DELETE FROM commercial.service_family_text",
                "UPDATE commercial.service_family_text SET status = 'DRAFT' WHERE status = 'SUPERSEDED'",
            ):
                with pytest.raises(DBAPIError):
                    async with eng.begin() as c:
                        await c.execute(text(sql))

    asyncio.run(immutable())
