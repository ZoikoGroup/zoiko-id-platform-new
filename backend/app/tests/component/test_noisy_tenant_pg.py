"""A noisy tenant cannot starve a quiet one (ZID-GAP-001 WP-24; attack catalog row 15: 'rate isolation and graceful denial').

The existing usage test proves the per-tenant limit one request at a time. This one is concurrent: tenant A fires a burst far above its allowance at
the same moment tenant B makes a steady run of ordinary calls, both through the real app against PostgreSQL. A is refused with 429 and a Retry-After
(graceful denial, never a 5xx); B is never refused and its latency stays bounded while A is being refused.
"""

from __future__ import annotations

import asyncio
import time

import httpx
from conftest import CELL
from onboarding_fixture import AUD, ISS, onboard
from zid_platform.main import create_app
from zid_platform.settings import Settings

LIMIT = 40
BURST = 400  # ten times A's allowance, all at once
QUIET = 30  # B stays well inside its own allowance
URL = "/v1/usage/limits"


def test_a_flooding_tenant_is_refused_and_a_quiet_one_is_not_affected(rt_url: str) -> None:
    a = onboard(rt_url, extra_actions=["metering.read"], extra_resources=["usage:*"])
    b = onboard(rt_url, extra_actions=["metering.read"], extra_resources=["usage:*"])
    b.ring = a.ring  # one issuer key for both tenants
    app = create_app(
        Settings(
            database_url=rt_url, token_issuer=ISS, token_audience=AUD, public_base_url=AUD,
            environment="test", cell_id="uk-1", cell_uuid=CELL, api_rate_limit_per_minute=LIMIT,
        ),
        keyring=a.ring,
    )  # fmt: skip
    ha, hb = a.bearer(a.owner), b.bearer(b.owner)

    async def run() -> tuple[list[int], list[int], list[float], str | None]:
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url=ISS, timeout=60) as client:
                quiet_lat: list[float] = []

                async def call(headers: dict[str, str], lat: list[float] | None = None) -> httpx.Response:
                    started = time.perf_counter()
                    r = await client.get(URL, headers=headers)
                    if lat is not None:
                        lat.append(time.perf_counter() - started)
                    return r

                async def quiet_tenant() -> list[httpx.Response]:
                    out = []
                    for _ in range(QUIET):  # steady ordinary traffic while the flood is going on
                        out.append(await call(hb, quiet_lat))
                        await asyncio.sleep(0.005)
                    return out

                flood = [call(ha) for _ in range(BURST)]
                results = await asyncio.gather(*flood, quiet_tenant())
                a_resp, b_resp = results[:BURST], results[BURST]
                retry_after = next((r.headers.get("retry-after") for r in a_resp if r.status_code == 429), None)
                return [r.status_code for r in a_resp], [r.status_code for r in b_resp], quiet_lat, retry_after

    a_codes, b_codes, b_latency, retry_after = asyncio.run(run())

    assert a_codes.count(200) <= LIMIT  # the noisy tenant got no more than its allowance
    assert a_codes.count(429) >= BURST - LIMIT - 5  # and the rest were refused, not queued or dropped
    assert set(a_codes) <= {200, 429}  # graceful denial: never a 5xx under the flood
    assert retry_after is not None and int(retry_after) >= 1  # told when to come back
    assert b_codes == [200] * QUIET  # the quiet tenant is never refused
    assert sorted(b_latency)[int(0.95 * QUIET) - 1] < 5.0  # and is not starved while A is being refused
