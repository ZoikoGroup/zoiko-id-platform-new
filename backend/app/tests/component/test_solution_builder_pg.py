"""The solution builder's APIs on Postgres (ZID-COM-WF-2026-11 WF-C01, WF-C02): offers by market, dependencies, preview, saved drafts and quote requests.

No price appears anywhere. A draft is not an entitlement, is isolated per company, is immutable, expires, and when sent for a quote freezes the scope and the
market exceptions it was judged with.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from conftest import ctx, engine
from fastapi.testclient import TestClient
from onboarding_fixture import ISS, onboard
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_commercial_pg import settings
from test_market_matrix_pg import full_row, signed
from zid_db import tenant_transaction
from zid_platform.main import create_app
from zid_platform.modules.commercial.ports import market_matrix_store as matrices
from zid_platform.modules.commercial.ports import solution_store as store
from zid_platform.modules.principal.ports import (
    Principal,
    PrincipalStatus,
    PrincipalType,
    create_principal,
)

ACTIONS = ["commercial.read", "commercial.plan.request"]
BODY = {
    "purpose": "ONBOARDING",
    "families": ["IDV", "SIGN", "API"],
    "subject_country": "NL",
    "payer_country": "NL",
    "operating_region": "eu-central",
    "volumes": {"IDV": {"expected_units": 5000, "rechecks_per_year": 1}},
}
OFFERS = "/v1/commercial/offers"
PREVIEW = "/v1/commercial/quotes/preview"
DRAFTS = "/v1/commercial/configurations"


async def activate_api_de(rt_url: str) -> None:
    author, activator = str(uuid.uuid4()), str(uuid.uuid4())
    async with engine(rt_url) as eng, eng.begin() as c:
        made = await matrices.create_draft(c, copy_of=None, created_by=author, note="builder")
        mid = made["matrix_id"]
        await matrices.put_row(c, mid, full_row(country="NL"))
        await signed(c, mid, author)
        now = datetime.now(UTC) - timedelta(seconds=5)
        await matrices.activate(c, mid, activator=activator, effective_from=now, now=now)


def cards(http: TestClient, query: str, headers: dict[str, str]) -> dict[str, dict]:
    out = http.get(f"{OFFERS}?{query}", headers=headers).json()["cards"]
    return {c["family"]: c for c in out}


def test_offers_dependencies_preview_and_drafts_never_show_a_price(rt_url: str) -> None:
    o = onboard(rt_url, extra_actions=ACTIONS)
    app = create_app(settings(rt_url), keyring=o.ring)
    adm = o.bearer(o.owner)
    with TestClient(app, base_url=ISS) as http:
        assert http.get(OFFERS).status_code == 401
        deps = http.get("/v1/commercial/capability-dependencies").json()  # public
        sign = next(f for f in deps["families"] if f["family"] == "SIGN")
        assert sign["depends_on"] == ["IDV"] and sign["regulated"] is True

        # nothing active: no market named is MARKET_REQUIRED, a named market is a conversation, never a price
        bare = http.get(OFFERS, headers=adm).json()
        assert {c["state"] for c in bare["cards"]} == {"MARKET_REQUIRED"} and len(
            bare["cards"]
        ) == 16
        named = http.get(f"{OFFERS}?market=NL", headers=adm).json()
        assert {c["state"] for c in named["cards"]} == {"CONTACT_SALES"}
        assert all(c["market"] is None for c in named["cards"])
        assert "$" not in str(named)
        assert http.get(f"{OFFERS}?market=germany", headers=adm).status_code == 422

        asyncio.run(activate_api_de(rt_url))
        de = cards(http, "market=NL&operating_region=eu-central", adm)
        assert de["API"]["state"] == "AVAILABLE" and de["API"]["reasons"] == []
        assert de["API"]["market"]["seller_entity"] == "Zoiko Tech GmbH"
        assert de["API"]["market"]["currency"] == "EUR"
        assert de["SIGN"]["state"] == "CONTACT_SALES", "no row recorded for it: nobody has said no"
        assert de["SIGN"]["provider_attribution"].startswith(
            "A regulated service is provided by an authorised partner"
        )
        fr = cards(http, "market=BE&operating_region=eu-central", adm)
        assert fr["API"]["state"] != "AVAILABLE", (
            "a country with no row is never inferred from another"
        )

        # preview: canonical scope and hash, what needs a quote, no price, nothing saved
        p1 = http.post(PREVIEW, headers=adm, json=BODY)
        assert p1.status_code == 200, p1.text
        prev = p1.json()
        assert prev["binding"] is False and prev["estimate"] is None and "$" not in str(prev)
        assert set(prev["request_quote"]) == {"IDV", "SIGN"} and prev["configurable"] is True
        reordered = {**BODY, "families": ["API", "SIGN", "IDV", "IDV"]}
        assert (
            http.post(PREVIEW, headers=adm, json=reordered).json()["scope_hash"]
            == prev["scope_hash"]
        )
        changed = {**BODY, "volumes": {"IDV": {"expected_units": 5001}}}
        assert (
            http.post(PREVIEW, headers=adm, json=changed).json()["scope_hash"] != prev["scope_hash"]
        )
        assert http.get(DRAFTS, headers=adm).json()["items"] == []

        # a signature without a verified identity is named; a bad volume, family or purpose is a 422
        nosig = http.post(
            PREVIEW, headers=adm, json={**BODY, "families": ["SIGN"], "volumes": {}}
        ).json()
        assert nosig["configurable"] is False and nosig["problems"][0]["needs"] == "IDV"
        for bad in (
            {"families": ["GHOST"]},
            {"volumes": {"IDV": {"expected_units": -3}}},
            {"purpose": "FUN"},
            {"price": 1},
        ):
            assert http.post(PREVIEW, headers=adm, json={**BODY, **bad}).status_code == 422, bad

        # save: a draft, not an entitlement; one company never sees another one's
        saved = http.post(DRAFTS, headers=adm, json=BODY)
        assert saved.status_code == 201, saved.text
        d = saved.json()
        assert (
            d["status"] == "DRAFT"
            and d["state"] == "DRAFT"
            and d["scope_hash"] == prev["scope_hash"]
        )
        got = http.get(f"{DRAFTS}/{d['draft_id']}", headers=adm)
        assert got.status_code == 200 and got.json()["families"] == ["API", "IDV", "SIGN"]
        other = onboard(rt_url, extra_actions=ACTIONS)
        other_app = create_app(settings(rt_url), keyring=other.ring)
        with TestClient(other_app, base_url=ISS) as http2:
            theirs = other.bearer(other.owner)
            assert http2.get(f"{DRAFTS}/{d['draft_id']}", headers=theirs).status_code == 404
            assert http2.get(DRAFTS, headers=theirs).json()["items"] == []

        # send for a quote: scope and exceptions frozen, once
        q = http.post(f"{DRAFTS}/{d['draft_id']}:request-quote", headers=adm)
        assert q.status_code == 200, q.text
        sent = q.json()
        assert (
            sent["status"] == "QUOTE_REQUESTED"
            and sent["quote_snapshot"]["scope_hash"] == prev["scope_hash"]
        )
        assert {e["family"] for e in sent["quote_snapshot"]["exceptions"]} == {"IDV", "SIGN"}
        again = http.post(f"{DRAFTS}/{d['draft_id']}:request-quote", headers=adm)
        assert (
            again.status_code == 409
            and again.json()["error"]["code"] == "SOLUTION_QUOTE_ALREADY_REQUESTED"
        )
        assert http.post(f"{DRAFTS}/{uuid.uuid4()}:request-quote", headers=adm).status_code == 404

    # the deal desk (operator plane) sees the request across companies; an ordinary scope sees only its own
    async def desk() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(str(uuid.uuid4()))) as c:
            items = await store.operator_requests(c)
            assert any(i["draft_id"] == d["draft_id"] for i in items)
            assert await store.mine(c) == []

    asyncio.run(desk())


def test_a_draft_is_immutable_never_deleted_and_an_expired_one_cannot_be_sent(
    rt_url: str, owner_url: str
) -> None:
    o = onboard(rt_url, extra_actions=ACTIONS)
    app = create_app(settings(rt_url), keyring=o.ring)
    adm = o.bearer(o.owner)
    with TestClient(app, base_url=ISS) as http:
        d = http.post(DRAFTS, headers=adm, json=BODY).json()
        nobody = str(uuid.uuid4())

        async def seed() -> None:
            async with engine(rt_url) as eng, tenant_transaction(eng, ctx(o.tenant)) as c:
                await create_principal(
                    c,
                    Principal(
                        nobody, o.tenant, PrincipalType.HUMAN, PrincipalStatus.ACTIVE, "uk-1"
                    ),
                )

        asyncio.run(seed())
        r = http.post(DRAFTS, headers=o.bearer(nobody), json=BODY)
        assert r.status_code == 403, "a principal with no grant cannot save or send a configuration"

    async def run() -> None:
        async with engine(owner_url) as eng:
            for sql in (
                "UPDATE commercial.solution_draft SET purpose = 'COMPLIANCE' WHERE draft_id = :i",
                "UPDATE commercial.solution_draft SET status = 'QUOTE_REQUESTED' WHERE draft_id = :i",
                "DELETE FROM commercial.solution_draft WHERE draft_id = :i",
            ):
                with pytest.raises(DBAPIError):
                    async with eng.begin() as c:
                        await c.execute(text(sql), {"i": uuid.UUID(d["draft_id"])})
        # a draft saved 40 days ago has expired: it reads as EXPIRED and is refused when sent
        old = uuid.uuid4()
        async with engine(owner_url) as eng, eng.begin() as c:
            await c.execute(
                text(
                    "INSERT INTO commercial.solution_draft (draft_id, tenant_id, created_by, purpose, families, scope_hash, created_at, expires_at) "
                    "VALUES (:i, :t, :t, 'ONBOARDING', ARRAY['API'], :h, now() - interval '40 days', now() - interval '10 days')"
                ),
                {"i": old, "t": uuid.UUID(o.tenant), "h": "a" * 64},
            )
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(o.tenant)) as c:
            found = await store.get(c, str(old))
            assert found is not None and found["state"] == "EXPIRED"
            with pytest.raises(Exception) as e:
                await store.request_quote(c, str(old), requested_by=o.owner, snapshot={})
            assert getattr(e.value, "code", "") == "SOLUTION_DRAFT_EXPIRED"

    asyncio.run(run())
