"""The public pricing page's APIs on Postgres (ZID-UX-PRICE-004 WF-P01, P06, P07; QA P01-P05, P32, P35, P38): public, never cached, never a price, market judged per dimension,
and an inquiry that is only ever a conversation."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from conftest import CELL, engine
from fastapi.testclient import TestClient
from onboarding_fixture import AUD, ISS, onboard
from operator_fixture import activate_grant, operators
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_commercial_pg import settings
from test_market_matrix_pg import full_row, go_live, signed
from zid_errors import ZidError
from zid_platform.main import create_app
from zid_platform.modules.commercial.api import commercial as api
from zid_platform.modules.commercial.application import pricing_governance as gov
from zid_platform.modules.commercial.domain import sales_lead as sl
from zid_platform.modules.commercial.ports import lead_store
from zid_platform.modules.commercial.ports import market_matrix_store as matrices
from zid_platform.modules.operator_access.domain.model import OperatorRole
from zid_platform.settings import Settings


@pytest.fixture(autouse=True)
def _fresh_address_limiter():
    """The per-address limit on public inquiries is process-wide; each test starts with it empty."""
    api._lead_recent.clear()
    yield
    api._lead_recent.clear()


PAGE = "/v1/public/pricing/page"
AVAIL = "/v1/public/pricing/availability"
LEADS = "/v1/public/pricing/leads"


def uid() -> str:
    return str(uuid.uuid4())


async def release_fi(rt_url: str) -> None:
    """API and SIGN rows for Finland, both rolled out; SIGN's provider rights and regulated status have already expired."""
    author, activator = uid(), uid()
    gone = datetime.now(UTC).date() - timedelta(days=1)
    async with engine(rt_url) as eng, eng.begin() as c:
        mid = (await matrices.create_draft(c, copy_of=None, created_by=author, note="public page"))[
            "matrix_id"
        ]
        await matrices.put_row(c, mid, full_row("API", "FI"))
        await matrices.put_row(
            c,
            mid,
            replace(
                full_row("SIGN", "FI"),
                provider_rights_ref="rights:p",
                regulated_status_ref="eutl:x",
                provider_rights_expires=gone,
                regulated_status_expires=gone,
            ),
        )
        future = datetime.now(UTC).date() + timedelta(days=200)
        await matrices.put_row(
            c,
            mid,
            replace(
                full_row("IDV", "FI"),
                provider_rights_ref="rights:idv",
                provider_rights_expires=future,
                document_issuer_countries=("DK", "SE"),
            ),
        )
        await signed(c, mid, author)
        now = datetime.now(UTC) - timedelta(seconds=5)
        await matrices.activate(c, mid, activator=activator, effective_from=now, now=now)
        await go_live(c, "API", "FI", through="CANARY")
        await go_live(c, "SIGN", "FI", through="CANARY")
        await go_live(c, "IDV", "FI", through="CANARY")


def lead_body(**over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "kind": "ENTERPRISE", "contact_name": "Ana Silva", "work_email": f"ana{uuid.uuid4().hex[:6]}@example.com", "company": "Acme Bank", "country": "PT",
        "capabilities": ["KYB", "IDV"], "volume_band": "FROM_10K_TO_100K", "entities": 12, "notice_version": gov.DRAFT_NOTICE_VERSION,
    }  # fmt: skip
    return {**base, **over}


def test_the_page_structure_and_availability_are_public_never_cached_and_never_a_price(
    rt_url: str,
) -> None:
    app = create_app(settings(rt_url), keyring=onboard(rt_url).ring)
    with TestClient(app, base_url=ISS) as http:
        page = http.get(PAGE)  # no token
        assert page.status_code == 200 and page.headers["cache-control"] == "no-store"
        body = page.json()
        assert body["packaging_status"] in ("PROPOSED", "APPROVED") and [
            t["code"] for t in body["tiers"]
        ] == [
            "ESSENTIALS",
            "BUSINESS",
            "ENTERPRISE",
        ]
        assert (
            len(body["families"]) == 16
            and len(body["use_cases"]) == 8
            and {p["code"] for p in body["paths"]} == {"SANDBOX", "PARTNER"}
        )
        assert "$" not in page.text

        # no market named: every service asks for one, nothing is inferred (P01)
        bare = http.get(AVAIL)
        assert bare.status_code == 200 and bare.headers["cache-control"] == "no-store"
        assert {c["state"] for c in bare.json()["cards"]} == {"MARKET_REQUIRED"}

        asyncio.run(release_fi(rt_url))
        q = "subject_country=FI&payer_country=FI&operating_region=eu-central&issuer_country=SE"
        out = http.get(f"{AVAIL}?{q}").json()
        cards = {c["family"]: c for c in out["cards"]}
        assert cards["API"]["state"] == "AVAILABLE" and cards["API"]["reasons"] == []
        assert cards["API"]["terms"] == {
            "seller_entity": "Zoiko Tech GmbH",
            "currency": "EUR",
            "data_region": "eu-central",
        }
        assert cards["SIGN"]["state"] == "RESTRICTED" and {"PROVIDER", "LEGAL"} <= set(
            cards["SIGN"]["reason_codes"]
        ), "an expired right is told apart from a missing one"
        assert cards["KYB"]["state"] == "SALES_ASSISTED", (
            "no row recorded: nobody has said no, so ask"
        )
        assert "FI" in out["released_markets"] and out["released_regions"] == ["eu-central"], (
            "only rows taken live through the rollout are offered"
        )
        assert out["dimensions"]["issuer_country"]["value"] == "SE"
        assert out["dimensions"]["issuer_country"]["judged"] is True
        assert cards["IDV"]["state"] == "AVAILABLE", (
            "documents issued in Sweden are covered by the Finnish row"
        )
        # the document issuer's country is its own dimension: one the row does not list is refused for a document-based service, and never for one that does not depend on it
        base_q = "subject_country=FI&payer_country=FI&operating_region=eu-central"
        norway = {
            c["family"]: c for c in http.get(f"{AVAIL}?{base_q}&issuer_country=NO").json()["cards"]
        }
        assert (
            norway["IDV"]["state"] == "RESTRICTED" and "PROVIDER" in norway["IDV"]["reason_codes"]
        )
        assert norway["API"]["state"] == "AVAILABLE"
        unnamed = http.get(f"{AVAIL}?{base_q}").json()
        assert {c["family"]: c for c in unnamed["cards"]}["IDV"]["state"] == "AVAILABLE"
        assert unnamed["dimensions"]["issuer_country"]["judged"] is False
        assert (
            "merchant_route" not in str(out) and "tax_class" not in str(out) and "$" not in str(out)
        )

        # P02: the payer, the subject and the region are separate. A payer who is not named never defaults to the subject
        no_payer = {
            c["family"]: c
            for c in http.get(f"{AVAIL}?subject_country=FI&operating_region=eu-central").json()[
                "cards"
            ]
        }
        assert (
            no_payer["API"]["state"] == "SALES_ASSISTED"
            and "COUNTRY" in no_payer["API"]["reason_codes"]
        )
        other_payer = {
            c["family"]: c
            for c in http.get(
                f"{AVAIL}?subject_country=FI&payer_country=SE&operating_region=eu-central"
            ).json()["cards"]
        }
        assert other_payer["API"]["state"] != "AVAILABLE", (
            "a foreign payer is not accepted because the subject is domestic"
        )

        # P36: pausing withdraws it at once and the page shows it, with nothing cached
        async def pause() -> None:
            from zid_platform.modules.commercial.ports import rollout_store

            async with engine(rt_url) as eng, eng.begin() as c:
                await rollout_store.move(
                    c,
                    family="API",
                    country="FI",
                    target="PAUSED",
                    decider=uid(),
                    reason="provider degraded",
                )

        asyncio.run(pause())
        after = {c["family"]: c for c in http.get(f"{AVAIL}?{q}").json()["cards"]}
        assert after["API"]["state"] == "NOT_AVAILABLE"

        # validation is at the door
        assert http.get(f"{AVAIL}?subject_country=Finland").status_code == 422
        assert http.get(f"{AVAIL}?subject_country=FI&price=1").status_code == 200, (
            "an unknown parameter is ignored, never turned into a price"
        )


def test_an_inquiry_is_a_conversation_only(rt_url: str, owner_url: str) -> None:
    app = create_app(settings(rt_url), keyring=onboard(rt_url).ring)

    async def footprint() -> dict[str, int]:
        async with engine(owner_url) as eng, eng.begin() as c:
            out = {}
            for table in (
                "sales_order",
                "entitlement",
                "activation_payment",
                "account",
                "invoice",
                "partner",
                "purchase_order",
            ):
                out[table] = (
                    await c.execute(text(f"SELECT count(*) FROM commercial.{table}"))
                ).scalar_one()
            return out

    before = asyncio.run(footprint())
    with TestClient(app, base_url=ISS) as http:
        nv = http.get(PAGE).json()["notice"]["version"]
        good = lead_body(notice_version=nv)
        r = http.post(LEADS, json=good)
        assert r.status_code == 201 and r.headers["cache-control"] == "no-store", r.text
        ref = r.json()["reference"]
        assert (
            r.json()["status"] == "RECEIVED"
            and "not created an account, a quote, a contract or a payment" in r.json()["message"]
        )
        assert "work_email" not in r.text, "the answer carries no personal data back"

        again = http.post(LEADS, json={**good, "contact_name": "A. Silva"})
        assert (
            again.status_code == 200
            and again.json()["reference"] == ref
            and again.json()["duplicate"] is True
        )

        # a robot's hidden field: answered like a success, stored nowhere
        bot = http.post(LEADS, json=lead_body(notice_version=nv, website="http://spam.example"))
        assert bot.status_code == 201 and bot.json()["reference"] != ref

        # no identity number, link, unknown service or extra field gets in (P28)
        for bad in (
            {"security_needs": "passport 123456789"},
            {"referral_source": "see https://evil.example"},
            {"capabilities": ["GHOST"]},
            {"kind": "PARTNER"},
        ):
            assert http.post(LEADS, json=lead_body(notice_version=nv, **bad)).status_code == 422, (
                bad
            )
        assert (
            http.post(LEADS, json={**lead_body(notice_version=nv), "tenant_id": uid()}).status_code
            == 422
        )
        assert (
            http.post(
                LEADS, json={**lead_body(notice_version=nv), "passport_number": "X"}
            ).status_code
            == 422
        )

    async def stored() -> list[tuple[str, str]]:
        async with engine(owner_url) as eng, eng.begin() as c:
            rows = (
                await c.execute(
                    text(
                        "SELECT lead_id::text, status FROM commercial.sales_lead WHERE lead_id = :i"
                    ),
                    {"i": uuid.UUID(ref)},
                )
            ).all()
            return [tuple(r) for r in rows]  # type: ignore[misc]

    assert asyncio.run(stored()) == [(ref, "RECEIVED")]
    assert asyncio.run(footprint()) == before, (
        "no company, entitlement, order, payment, invoice, partner or purchase order appeared"
    )


def test_the_per_address_and_per_email_limits_and_the_database_rules(
    rt_url: str, owner_url: str
) -> None:
    fake = SimpleNamespace(client=SimpleNamespace(host=f"198.51.100.{uuid.uuid4().int % 200}"))
    for _ in range(10):
        api._lead_rate_limit(fake)  # type: ignore[arg-type]
    with pytest.raises(ZidError) as e:
        api._lead_rate_limit(fake)  # type: ignore[arg-type]
    assert e.value.http_status == 429

    async def run() -> None:
        email = f"cap{uuid.uuid4().hex[:6]}@example.com"

        def make(i: int) -> sl.Lead:
            return sl.check_lead(
                kind="ENTERPRISE", contact_name="Cap Test", work_email=email, company=f"Company {chr(65 + i)}", country="PT", region=None, capabilities=[],
                volume_band="UNSURE", entities=None, referral_source=None, security_needs=None, partner_type=None, notice_version="n1", honeypot=None, known_families={"IDV"},
            )  # fmt: skip

        async with engine(rt_url) as eng, eng.begin() as c:
            for i in range(lead_store.MAX_PER_EMAIL_PER_DAY):
                assert (await lead_store.submit(c, make(i)))["duplicate"] is False
            with pytest.raises(Exception) as err:
                await lead_store.submit(c, make(9))
            assert getattr(err.value, "code", "") == "LEAD_RATE_LIMITED"
        async with engine(owner_url) as eng:
            for sql in (
                "UPDATE commercial.sales_lead SET work_email = 'x@example.com'",
                "DELETE FROM commercial.sales_lead",
                "UPDATE commercial.sales_lead SET status = 'OFFER_SENT' WHERE status = 'RECEIVED'",
            ):
                with pytest.raises(DBAPIError):
                    async with eng.begin() as c:
                        await c.execute(text(sql))

    asyncio.run(run())


def test_the_operator_queue_moves_a_lead_forward_only(rt_url: str) -> None:
    maker, bob, carol = uid(), uid(), uid()
    ops = operators(
        rt_url,
        {
            maker: {OperatorRole.REQUESTER},
            bob: {OperatorRole.APPROVER},
            carol: {OperatorRole.APPROVER},
        },
    )
    app = create_app(
        Settings(
            database_url=rt_url, token_issuer=ISS, token_audience=AUD, public_base_url=AUD, environment="test", cell_id="uk-1", cell_uuid=CELL,
            operator_tenant_id=ops.tenant, operator_client_ids=(ops.client,),
        ),
        keyring=ops.ring,
    )  # fmt: skip
    with TestClient(app, base_url=ISS) as http:
        nv = http.get(PAGE).json()["notice"]["version"]
        ref = http.post(LEADS, json=lead_body(notice_version=nv, company="Queue Test Co")).json()[
            "reference"
        ]
        h = ops.bearer(maker)
        activate_grant(
            http,
            ops,
            maker,
            (bob, carol),
            capability="commercial.catalog.manage",
            resource_scope="commercial:catalogue",
        )
        listed = {
            i["lead_id"]: i
            for i in http.get("/v1/commercial/sales-leads", headers=h).json()["items"]
        }
        assert listed[ref]["status"] == "RECEIVED" and listed[ref]["capabilities"] == ["IDV", "KYB"]

        def move(to: str, reason: str | None = None) -> int:
            return http.post(
                f"/v1/commercial/sales-leads/{ref}:advance",
                json={"status": to, **({"reason": reason} if reason else {})},
                headers=h,
            ).status_code

        assert move("SCOPED") == 409, "a step cannot be skipped"
        assert move("QUALIFIED") == 200 and move("SCOPED") == 200 and move("REVIEW") == 200
        assert move("QUALIFIED") == 409, "nothing moves backwards"
        assert move("CLOSED") == 422, "closing needs a reason"
        assert move("CLOSED", "no budget this year") == 200
        assert move("OFFER_SENT") == 409, "a closed inquiry is final"
        assert (
            http.post(
                f"/v1/commercial/sales-leads/{uuid.uuid4()}:advance",
                json={"status": "QUALIFIED"},
                headers=h,
            ).status_code
            == 404
        )
