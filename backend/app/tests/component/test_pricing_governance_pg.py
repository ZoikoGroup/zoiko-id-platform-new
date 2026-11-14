"""Who approved the public pricing page, and which privacy notice it serves (ZID-UX-PRICE-004 section 09, WF-P07): the page is APPROVED only by two different appointed signers on the current
file's hash; any change unsigns it; the privacy notice is Legal's text, activated by a different person; and an inquiry that cites a notice that is no longer the one served is refused."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

import pytest
from conftest import engine
from fastapi.testclient import TestClient
from finance_people import appoint_person
from onboarding_fixture import ISS, onboard
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_commercial_pg import settings
from test_public_pricing_pg import LEADS, PAGE, _fresh_address_limiter, lead_body  # noqa: F401
from zid_platform.main import create_app
from zid_platform.modules.commercial.application import pricing_governance as gov
from zid_platform.modules.commercial.domain.catalogue import CommercialError
from zid_platform.modules.commercial.ports import notice_store as notices


def uid() -> str:
    return str(uuid.uuid4())


async def refused(conn, call, code: str) -> None:
    with pytest.raises(CommercialError) as e:
        async with conn.begin_nested():
            await call()
    assert e.value.code == code, repr(e.value)


def test_the_page_is_approved_only_by_a_founder_and_a_cco_on_the_current_hash(
    rt_url: str, owner_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    o = onboard(rt_url)
    app = create_app(settings(rt_url), keyring=o.ring)
    founder, cco, nobody = uid(), uid(), uid()

    async def sign_all() -> None:
        async with engine(rt_url) as eng:
            await appoint_person(eng, founder, "FOUNDER")
            await appoint_person(eng, cco, "CCO")
            await appoint_person(eng, nobody, "REVOPS")
            async with eng.begin() as c:
                h = gov.content_hash()
                before = await gov.page_state(c)
                assert before["status"] == "PROPOSED" and before["missing"] == ["CCO", "FOUNDER"]
                await refused(
                    c, lambda: gov.sign_page(c, signer=founder, shown_hash="0" * 64), "PAGE_CHANGED"
                )
                await refused(
                    c, lambda: gov.sign_page(c, signer=nobody, shown_hash=h), "NOT_A_SIGNATORY"
                )
                half = await gov.sign_page(c, signer=founder, shown_hash=h)
                assert half["status"] == "PROPOSED" and half["missing"] == ["CCO"]
                await refused(
                    c, lambda: gov.sign_page(c, signer=founder, shown_hash=h), "ALREADY_SIGNED"
                )
                done = await gov.sign_page(c, signer=cco, shown_hash=h)
                assert done["status"] == "APPROVED" and done["signed_by"] == ["CCO", "FOUNDER"]

    with TestClient(app, base_url=ISS) as http:
        assert http.get(PAGE).json()["packaging_status"] == "PROPOSED"
        asyncio.run(sign_all())
        approved = http.get(PAGE).json()
        assert approved["packaging_status"] == "APPROVED" and approved["approval"] == {
            "signed_by": ["CCO", "FOUNDER"],
            "missing": [],
        }

        # the signature is on the file as it was: a later change to the file unsigns it, so the notice returns by itself
        monkeypatch.setattr(gov, "content_hash", lambda: "f" * 64)
        assert http.get(PAGE).json()["packaging_status"] == "PROPOSED"
        monkeypatch.undo()
        assert http.get(PAGE).json()["packaging_status"] == "APPROVED"

    async def immutable() -> None:
        async with engine(owner_url) as eng:
            for sql in (
                "UPDATE commercial.pricing_page_signature SET signer_role = 'FOUNDER'",
                "DELETE FROM commercial.pricing_page_signature",
            ):
                with pytest.raises(DBAPIError):
                    async with eng.begin() as c:
                        await c.execute(text(sql))

    asyncio.run(immutable())


def test_the_privacy_notice_is_legals_text_activated_by_someone_else_and_an_old_version_is_refused(
    rt_url: str, owner_url: str
) -> None:
    o = onboard(rt_url)
    app = create_app(settings(rt_url), keyring=o.ring)
    author, checker = uid(), uid()
    version = f"inquiry-{uuid.uuid4().hex[:6]}"
    body = "We use your details only to answer your inquiry, keep them for as long as the conversation lasts, and never use them for anything else. Do not send identity documents."

    with TestClient(app, base_url=ISS) as http:
        shown = http.get(PAGE).json()["notice"]
        assert shown["status"] == "DRAFT" and shown["version"] == gov.DRAFT_NOTICE_VERSION, (
            "until Legal's text is active the draft is served and marked"
        )
        assert http.post(LEADS, json=lead_body(notice_version=shown["version"])).status_code == 201
        assert http.post(LEADS, json=lead_body(notice_version="made-up-version")).status_code == 409

        async def propose_and_activate() -> str:
            async with engine(rt_url) as eng, eng.begin() as c:
                await refused(
                    c,
                    lambda: notices.propose(c, version="x", body="too short", created_by=author),
                    "NOTICE_REFUSED",
                )
                made = await notices.propose(c, version=version, body=body, created_by=author)
                await refused(
                    c,
                    lambda: notices.propose(c, version=version, body=body, created_by=author),
                    "NOTICE_VERSION_EXISTS",
                )
                await refused(
                    c,
                    lambda: notices.activate(
                        c, made["notice_id"], activator=author, now=datetime.now(UTC)
                    ),
                    "NOTICE_SELF_ACTIVATION",
                )
                assert (await gov.served_notice(c))["status"] == "DRAFT", (
                    "a draft changes nothing a visitor sees"
                )
                await notices.activate(
                    c, made["notice_id"], activator=checker, now=datetime.now(UTC)
                )
                await refused(c, lambda: notices.withdraw(c, made["notice_id"]), "NOTICE_NOT_DRAFT")
                return str(made["notice_id"])

        asyncio.run(propose_and_activate())
        live = http.get(PAGE).json()["notice"]
        assert live == {"version": version, "text": body, "status": "ACTIVE"}
        # a form loaded before the change cites the old version and is told to reload; the new version is accepted
        stale = http.post(LEADS, json=lead_body(notice_version=shown["version"]))
        assert stale.status_code == 409 and stale.json()["error"]["code"] == "NOTICE_VERSION_STALE"
        assert http.post(LEADS, json=lead_body(notice_version=version)).status_code == 201

    async def immutable() -> None:
        async with engine(owner_url) as eng:
            for sql in (
                "UPDATE commercial.public_notice SET body = 'rewritten after activation, which is not allowed at all' WHERE status = 'ACTIVE'",
                "DELETE FROM commercial.public_notice",
            ):
                with pytest.raises(DBAPIError):
                    async with eng.begin() as c:
                        await c.execute(text(sql))

    asyncio.run(immutable())
