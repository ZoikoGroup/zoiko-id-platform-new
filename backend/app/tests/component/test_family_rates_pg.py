"""The family rate card and the public estimate on Postgres (ZID-UX-PRICE-004 WF-P04, sections 06.1, 06.2): three different people sign, an active card is immutable, and the estimate prices only what the matrix has
released and the card has a rate for, never binding, never across currencies. The amounts are test fixtures, not product prices."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from conftest import CELL, ctx, engine
from fastapi.testclient import TestClient
from onboarding_fixture import AUD, ISS, onboard
from operator_fixture import activate_grant, operators
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_commercial_pg import settings
from test_market_matrix_pg import full_row, go_live, signed
from test_public_pricing_pg import AVAIL, _fresh_address_limiter, uid  # noqa: F401
from zid_db import tenant_transaction
from zid_platform.main import create_app
from zid_platform.modules.commercial.domain.family_rates import Rate
from zid_platform.modules.commercial.ports import family_rate_store as store
from zid_platform.modules.commercial.ports import finance
from zid_platform.modules.commercial.ports import market_matrix_store as matrices
from zid_platform.modules.operator_access.domain.model import OperatorRole
from zid_platform.settings import Settings

ESTIMATE = "/v1/public/pricing/estimate"
D = Decimal


async def refused(conn, call, code: str) -> None:
    with pytest.raises(Exception) as e:
        async with conn.begin_nested():
            await call()
    assert getattr(e.value, "code", None) == code, repr(e.value)


def line(family: str, period: str, **over: object) -> Rate:
    base: dict[str, object] = {
        "family": family, "country": "SK", "period": period, "currency": "EUR", "charge_unit": "completed check", "included_units": 100,
        "unit_amount": D("0.50"), "minimum_amount": D("49"), "minimum_model": "FLOOR",
    }  # fmt: skip
    return Rate(**{**base, **over})  # type: ignore[arg-type]


async def release_sk(rt_url: str) -> None:
    """API and IDV released for Slovakia; the matrix has no row for SIGN there."""
    author, activator = uid(), uid()
    async with engine(rt_url) as eng, eng.begin() as c:
        mid = (await matrices.create_draft(c, copy_of=None, created_by=author, note="rates"))[
            "matrix_id"
        ]
        for fam in ("API", "DOC"):
            await matrices.put_row(c, mid, full_row(fam, "SK"))
        await signed(c, mid, author)
        now = datetime.now(UTC) - timedelta(seconds=5)
        await matrices.activate(c, mid, activator=activator, effective_from=now, now=now)
        for fam in ("API", "DOC"):
            await go_live(c, fam, "SK", through="CANARY")


def test_the_card_needs_three_different_people_and_an_active_card_never_changes(
    rt_url: str, owner_url: str
) -> None:
    async def run() -> None:
        author, cfo, cco, founder, activator = (uid() for _ in range(5))
        async with engine(rt_url) as eng, eng.begin() as c:
            made = (await store.create_draft(c, copy_of=None, created_by=author, note="g1"))[
                "card_id"
            ]
            # a card with no lines cannot be activated, even fully signed
            empty = (await store.create_draft(c, copy_of=None, created_by=author, note="empty"))[
                "card_id"
            ]
            for role, who in (("CFO", cfo), ("CCO", cco), ("FOUNDER", founder)):
                await store.approve(c, empty, role=role, approver=who)
            await refused(
                c,
                lambda: store.activate(
                    c,
                    empty,
                    activator=activator,
                    effective_from=datetime.now(UTC),
                    now=datetime.now(UTC),
                ),
                "RATE_CARD_EMPTY",
            )
            await store.put_line(
                c, made, line("API", "MONTHLY", country="SI"), "signed rate card G1-2026-11 line 4"
            )
            await refused(
                c,
                lambda: store.approve(c, made, role="CFO", approver=author),
                "RATE_CARD_SELF_APPROVAL",
            )
            await refused(
                c,
                lambda: store.approve(c, made, role="CEO", approver=cfo),
                "RATE_CARD_ROLE_UNKNOWN",
            )
            await store.approve(c, made, role="CFO", approver=cfo)
            await refused(
                c,
                lambda: store.approve(c, made, role="CCO", approver=cfo),
                "RATE_CARD_PERSON_ALREADY_SIGNED",
            )
            await refused(
                c,
                lambda: store.approve(c, made, role="CFO", approver=cco),
                "RATE_CARD_ROLE_ALREADY_SIGNED",
            )
            await store.approve(c, made, role="CCO", approver=cco)
            now = datetime.now(UTC)
            await refused(
                c,
                lambda: store.activate(c, made, activator=activator, effective_from=now, now=now),
                "RATE_CARD_APPROVALS_MISSING",
            )
            await store.approve(c, made, role="FOUNDER", approver=founder)
            await refused(
                c,
                lambda: store.activate(c, made, activator=author, effective_from=now, now=now),
                "RATE_CARD_SELF_APPROVAL",
            )
            earlier = now - timedelta(seconds=10)
            await store.activate(c, made, activator=activator, effective_from=earlier, now=now)
            await refused(
                c,
                lambda: store.put_line(c, made, line("DOC", "MONTHLY", country="SI"), "x"),
                "RATE_CARD_NOT_DRAFT",
            )
            await refused(c, lambda: store.withdraw(c, made), "RATE_CARD_NOT_DRAFT")

            # the next version is a copy signed afresh and supersedes the first
            nxt = (await store.create_draft(c, copy_of=made, created_by=author, note=None))[
                "card_id"
            ]
            assert await store.approvals(c, nxt) == [] and [
                r.family for r, _ in await store.lines(c, nxt)
            ] == ["API"]
            await store.put_line(
                c, nxt, line("DOC", "MONTHLY", country="SI"), "signed rate card G1-2026-11 line 9"
            )
            for role, who in (("CFO", cfo), ("CCO", cco), ("FOUNDER", founder)):
                await store.approve(c, nxt, role=role, approver=who)
            await store.activate(
                c,
                nxt,
                activator=activator,
                effective_from=datetime.now(UTC) - timedelta(seconds=1),
                now=datetime.now(UTC),
            )
            old = await store.card(c, made)
            assert old is not None and old["status"] == "SUPERSEDED"

        async with engine(owner_url) as eng:
            for sql in (
                "UPDATE commercial.family_rate SET unit_amount = 0.01",
                "DELETE FROM commercial.family_rate",
                "UPDATE commercial.family_rate_card SET note = 'edited' WHERE status = 'ACTIVE'",
                "DELETE FROM commercial.family_rate_card WHERE status = 'ACTIVE'",
                "DELETE FROM commercial.family_rate_card_approval",
            ):
                with pytest.raises(DBAPIError):
                    async with eng.begin() as c:
                        await c.execute(text(sql))
            # the database repeats the rules: a minimum with no declared model, and a line with no price at all, cannot be written
            for sql in (
                (
                    "INSERT INTO commercial.family_rate (card_id, family, country, period, currency, charge_unit, included_units, unit_amount, minimum_amount, source_ref) "
                    "SELECT card_id, 'REV', 'SK', 'MONTHLY', 'EUR', 'reviewer action', 0, 1, 5, 'x' FROM commercial.family_rate_card WHERE status = 'DRAFT' LIMIT 1"
                ),
            ):
                with pytest.raises(DBAPIError):
                    async with eng.begin() as c:
                        await c.execute(text(sql))

    asyncio.run(run())


def test_the_estimate_prices_only_what_is_released_and_rated_and_is_never_binding(
    rt_url: str,
) -> None:
    app = create_app(settings(rt_url), keyring=onboard(rt_url).ring)
    base = {"subject_country": "SK", "payer_country": "SK", "operating_region": "eu-central"}
    with TestClient(app, base_url=ISS) as http:
        asyncio.run(release_sk(rt_url))

        # a released service with no rate recorded is Quote required, and says why: nothing is invented
        none = http.post(ESTIMATE, json={**base, "volumes": {"API": 1000}}).json()
        assert none["binding"] is False and none["lines"] == [] and none["totals"] == {}
        assert none["quote_required"][0]["state"] in ("NO_RATE_RECORDED",) and none["quote_hash"]

        async def price() -> None:
            author, cfo, cco, founder, activator = (uid() for _ in range(5))
            async with engine(rt_url) as eng, eng.begin() as c:
                card = (
                    await store.create_draft(c, copy_of=None, created_by=author, note="estimate")
                )["card_id"]
                await store.put_line(
                    c, card, line("API", "MONTHLY", country="SK"), "signed card line 1"
                )
                await store.put_line(
                    c,
                    card,
                    line(
                        "API",
                        "ANNUAL",
                        included_units=1200,
                        unit_amount=D("0.40"),
                        minimum_amount=D("400"),
                    ),
                    "signed card line 2",
                )
                await store.put_line(
                    c,
                    card,
                    line(
                        "DOC",
                        "MONTHLY",
                        included_units=0,
                        unit_amount=D("1.00"),
                        minimum_amount=D("20"),
                        minimum_model="PLATFORM_FEE",
                    ),
                    "signed card line 3",
                )
                for role, who in (("CFO", cfo), ("CCO", cco), ("FOUNDER", founder)):
                    await store.approve(c, card, role=role, approver=who)
                await store.activate(
                    c,
                    card,
                    activator=activator,
                    effective_from=datetime.now(UTC) - timedelta(seconds=3),
                    now=datetime.now(UTC),
                )

        asyncio.run(price())

        out = http.post(ESTIMATE, json={**base, "volumes": {"API": 1000, "DOC": 10, "SIGN": 5}})
        assert out.status_code == 200 and out.headers["cache-control"] == "no-store", out.text
        est = out.json()
        by = {ln["family"]: ln for ln in est["lines"]}
        assert (
            by["API"]["amount"] == "450.00"
            and by["API"]["billable_units"] == 900
            and by["API"]["minimum_model"] == "FLOOR"
        )
        assert (
            by["DOC"]["amount"] == "30.00" and "platform fee plus usage" in by["DOC"]["formula"]
        ), "a fee is added to usage, a floor is not"
        assert est["totals"] == {"EUR": "480.00"} and est["binding"] is False
        assert [q["family"] for q in est["quote_required"]] == ["SIGN"] and est["quote_required"][
            0
        ]["state"] == "SALES_ASSISTED", "no row for it in Slovakia"
        assert est["tax"]["state"] == "CALCULATED_AT_CHECKOUT" and est["rate_card"]["version"] >= 1
        assert datetime.fromisoformat(est["valid_until"]) > datetime.fromisoformat(est["as_of"])
        assert any("not an invoice or a quote" in a for a in est["assumptions"])

        # the same inputs give the same hash, and any change to the scope or the volume gives another
        again = http.post(
            ESTIMATE, json={**base, "volumes": {"DOC": 10, "SIGN": 5, "API": 1000}}
        ).json()
        assert again["quote_hash"] == est["quote_hash"] and again["as_of"] != ""
        assert (
            http.post(
                ESTIMATE, json={**base, "volumes": {"API": 1001, "DOC": 10, "SIGN": 5}}
            ).json()["quote_hash"]
            != est["quote_hash"]
        )

        # annual: priced on twelve times the volume, the saving computed from the two signed rates, and a service with no annual rate stays Quote required
        yearly = http.post(
            ESTIMATE, json={**base, "period": "ANNUAL", "volumes": {"API": 1000, "DOC": 10}}
        ).json()
        api_line = {ln["family"]: ln for ln in yearly["lines"]}["API"]
        assert api_line["volume"] == 12000 and api_line["amount"] == "4320.00"
        assert api_line["annual_comparison"] == {
            "currency": "EUR",
            "twelve_months": "5400.00",
            "annual": "4320.00",
            "saving": "1080.00",
        }
        assert [q["family"] for q in yearly["quote_required"]] == ["DOC"] and yearly[
            "quote_required"
        ][0]["state"] == "NO_RATE_RECORDED"

        # the payer decides the rate: unnamed or foreign, nothing is priced for a country with no row and no rate
        for payer in (None, "FR"):
            body = {
                k: v
                for k, v in {**base, "payer_country": payer, "volumes": {"API": 1000}}.items()
                if v is not None
            }
            r = http.post(ESTIMATE, json=body).json()
            assert (
                r["lines"] == []
                and r["totals"] == {}
                and r["quote_required"][0]["state"] == "SALES_ASSISTED"
            )

        # availability shows the rates and offers the monthly/annual choice only when every priced service has both
        av = http.get(
            f"{AVAIL}?subject_country=SK&payer_country=SK&operating_region=eu-central"
        ).json()
        cards = {c["family"]: c for c in av["cards"]}
        assert set(cards["API"]["rates"]) == {"MONTHLY", "ANNUAL"} and set(
            cards["DOC"]["rates"]
        ) == {"MONTHLY"}
        assert cards["API"]["rates"]["MONTHLY"] == {
            "currency": "EUR",
            "charge_unit": "completed check",
            "included_units": 100,
            "unit_amount": "0.500000",
            "minimum_amount": "49.000000",
            "minimum_model": "FLOOR",
        }
        assert av["rate_card"]["both_periods"] is False, (
            "DOC has no annual rate, so no toggle is offered"
        )
        assert cards["SIGN"]["rates"] == {}

        # validation at the door
        for bad in (
            {"volumes": {}},
            {"volumes": {"GHOST": 1}},
            {"volumes": {"API": -1}},
            {"volumes": {"API": 1}, "price": 1},
            {"volumes": {"API": 10**10}},
        ):
            assert http.post(ESTIMATE, json={**base, **bad}).status_code == 422, bad


def test_the_operator_rate_card_routes_import_all_or_nothing_and_check_the_appointment(
    rt_url: str,
) -> None:
    maker, cfo, bob, carol = uid(), uid(), uid(), uid()
    ops = operators(
        rt_url,
        {
            maker: {OperatorRole.REQUESTER},
            cfo: {OperatorRole.REQUESTER},
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

    async def appoint() -> None:
        # the CFO is an operator principal of the operator tenant, so the appointment is made in that tenant
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(ops.tenant)) as c:
            await finance.appoint(
                c,
                principal=cfo,
                role="CFO",
                appointed_by=uid(),
                effective_from=datetime.now(UTC) - timedelta(days=200),
            )

    asyncio.run(appoint())
    base = "/v1/commercial/family-rate-cards"
    row = {
        "family": "API",
        "country": "PL",
        "period": "MONTHLY",
        "currency": "EUR",
        "charge_unit": "completed check",
        "included_units": 10,
        "unit_amount": "0.25",
        "source_ref": "signed card line 7",
    }
    with TestClient(app, base_url=ISS) as http:
        for who in (maker, cfo):
            activate_grant(
                http,
                ops,
                who,
                (bob, carol),
                capability="commercial.catalog.manage",
                resource_scope="commercial:finance",
            )
        h, hc = ops.bearer(maker), ops.bearer(cfo)
        card = http.post(base, json={"note": "operator test"}, headers=h)
        assert card.status_code == 201, card.text
        cid = card.json()["card_id"]

        bad = http.post(
            f"{base}/{cid}/lines:import",
            headers=h,
            json={
                "lines": [
                    row,
                    {**row, "currency": "ZZZ"},
                    {**row, "family": "GHOST"},
                    {**row, "unit_amount": "0"},
                    {**row, "minimum_amount": "5"},
                ]
            },
        )
        assert bad.status_code == 422 and bad.json()["error"]["code"] == "RATE_IMPORT_REJECTED"
        issues = {d["field"]: d["issue"] for d in bad.json()["error"]["details"]}
        assert (
            "DUPLICATE_IN_IMPORT" in issues["line 2 API PL MONTHLY"]
            and "CURRENCY_UNSUPPORTED" in issues["line 2 API PL MONTHLY"]
        )
        assert (
            "FAMILY_UNKNOWN" in issues["line 3 GHOST PL MONTHLY"]
            and "NO_PRICE" in issues["line 4 API PL MONTHLY"]
            and "MINIMUM_MODEL_REQUIRED" in issues["line 5 API PL MONTHLY"]
        )
        assert http.get(f"{base}/{cid}", headers=h).json()["lines"] == [], "all or nothing"

        ok = http.post(
            f"{base}/{cid}/lines:import",
            headers=h,
            json={
                "lines": [
                    row,
                    {
                        **row,
                        "period": "ANNUAL",
                        "included_units": 120,
                        "unit_amount": "0.20",
                        "minimum_amount": "30",
                        "minimum_model": "FLOOR",
                    },
                ]
            },
        )
        assert ok.status_code == 200 and ok.json()["lines_written"] == 2, ok.text
        detail = http.get(f"{base}/{cid}", headers=h).json()
        assert [(r["period"], r["minimum_model"]) for r in detail["lines"]] == [
            ("ANNUAL", "FLOOR"),
            ("MONTHLY", None),
        ]
        assert detail["missing_approvals"] == ["CFO", "CCO", "FOUNDER"]

        # an approval needs the appointment, not just the capability; and the author is never an approver
        assert (
            http.post(f"{base}/{cid}:approve", json={"role": "CFO"}, headers=h).status_code == 403
        )
        done = http.post(f"{base}/{cid}:approve", json={"role": "CFO"}, headers=hc)
        assert (
            done.status_code == 200
            and done.json()["signed"] == ["CFO"]
            and done.json()["missing"] == ["CCO", "FOUNDER"]
        )
        assert (
            http.post(f"{base}/{cid}:approve", json={"role": "CFO"}, headers=hc).status_code == 409
        )
        assert http.post(f"{base}/{cid}:activate", json={}, headers=hc).status_code == 409, (
            "two signatures are missing"
        )
        assert http.get(base, headers=h).status_code == 200
        assert (
            http.post(
                f"{base}/{uuid.uuid4()}:approve", json={"role": "CFO"}, headers=hc
            ).status_code
            == 404
        )
