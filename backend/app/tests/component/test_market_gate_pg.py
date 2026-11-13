"""The market gate in front of a live paid call (ZID-COM-WF-2026-11 section 10.1, INV-01): judged against the active matrix, rights expiry and rollout, blocking only in enforce
mode, reported in shadow, needing the services' usage:write scope."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from conftest import engine
from fastapi.testclient import TestClient
from onboarding_fixture import ISS, onboard
from test_commercial_pg import settings
from test_market_matrix_pg import full_row, go_live, signed, uid
from zid_platform.main import create_app
from zid_platform.modules.commercial.ports import market_matrix_store as matrices
from zid_platform.modules.commercial.ports import rollout_store

GATE = "/v1/commercial/market-gate:check"
OK = {
    "family": "API",
    "subject_country": "PL",
    "payer_country": "PL",
    "operating_region": "eu-central",
}


async def release(rt_url: str, country: str = "PL") -> None:
    author, activator = uid(), uid()
    async with engine(rt_url) as eng, eng.begin() as c:
        mid = (await matrices.create_draft(c, copy_of=None, created_by=author, note="gate"))[
            "matrix_id"
        ]
        await matrices.put_row(c, mid, full_row("API", country))
        await signed(c, mid, author)
        now = datetime.now(UTC) - timedelta(seconds=5)
        await matrices.activate(c, mid, activator=activator, effective_from=now, now=now)
        await go_live(c, "API", country, through="CANARY")


def test_shadow_reports_and_does_not_block_and_the_scope_is_required(rt_url: str) -> None:
    o = onboard(rt_url)
    app = create_app(settings(rt_url), keyring=o.ring)  # deployment mode: shadow
    svc, plain = o.bearer(o.owner, scope="usage:write", client="svc"), o.bearer(o.owner)
    with TestClient(app, base_url=ISS) as http:
        assert http.post(GATE, json=OK).status_code == 401
        denied = http.post(GATE, json=OK, headers=plain)
        assert denied.status_code == 403 and denied.json()["error"]["code"] == "SCOPE_INSUFFICIENT"

        # nothing released: reported as not sellable but not blocked, with the causes named
        r = http.post(GATE, json=OK, headers=svc)
        assert r.status_code == 200, r.text
        out = r.json()
        assert (
            out["sellable"] is False
            and out["blocked"] is False
            and out["mode"] == "shadow"
            and out["causes"]
        )
        assert http.post(GATE, json={**OK, "family": "GHOST"}, headers=svc).status_code == 422
        assert http.post(GATE, json={**OK, "price": 1}, headers=svc).status_code == 422

        asyncio.run(release(rt_url))
        ok = http.post(GATE, json=OK, headers=svc).json()
        assert ok["sellable"] is True and ok["blocked"] is False and ok["refusals"] == []
        # a dimension nobody named is refused as not evaluated, even for a released family
        partial = http.post(
            GATE, json={"family": "API", "subject_country": "PL"}, headers=svc
        ).json()
        assert partial["sellable"] is False and "COUNTRY" in partial["causes"]


def test_enforce_blocks_an_unreleased_market_and_a_paused_one_with_the_causes_named(
    rt_url: str,
) -> None:
    o = onboard(rt_url)
    app = create_app(settings(rt_url, commercial_enforcement="enforce"), keyring=o.ring)
    svc = o.bearer(o.owner, scope="usage:write", client="svc")
    country = "CZ"
    body = {**OK, "subject_country": country, "payer_country": country}
    with TestClient(app, base_url=ISS) as http:
        blocked = http.post(GATE, json=body, headers=svc)
        assert (
            blocked.status_code == 403 and blocked.json()["error"]["code"] == "MARKET_NOT_RELEASED"
        )
        assert blocked.json()["error"]["details"], "the refusal names which dimension and why"

        asyncio.run(release(rt_url, country))
        allowed = http.post(GATE, json=body, headers=svc)
        assert (
            allowed.status_code == 200
            and allowed.json()["sellable"] is True
            and allowed.json()["mode"] == "enforce"
        )

        async def pause() -> None:
            async with engine(rt_url) as eng, eng.begin() as c:
                await rollout_store.move(
                    c,
                    family="API",
                    country=country,
                    target="PAUSED",
                    decider=uid(),
                    reason="provider degraded",
                )

        asyncio.run(pause())
        again = http.post(GATE, json=body, headers=svc)
        assert again.status_code == 403, "a paused market stops new paid operations at once"
