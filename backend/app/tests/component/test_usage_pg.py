"""API usage counters and the per-tenant rate limit over HTTP (CR-023; ZID-UX-001 §18).

Usage counts only calls that authenticated, by hour, surface, client and status class. The limit is per
tenant: one tenant over its allowance is told when to retry, and another tenant is not affected.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from admin_fixture import make_admin
from conftest import CELL, ctx, engine
from fastapi.testclient import TestClient
from onboarding_fixture import AUD, ISS, onboard
from zid_db import tenant_transaction
from zid_platform.main import create_app
from zid_platform.modules.metering.domain.usage import (
    RateLimiter,
    UsageBuffer,
    UsageKey,
    status_class,
    surface_for,
)
from zid_platform.settings import Settings

LIMIT = 8


def test_surface_status_and_buffer_are_coarse_and_bounded() -> None:
    assert surface_for("/v1/webhooks/endpoints/0190-abc:verify") == "webhooks"
    assert surface_for("/v1/alerts") == "alerts"
    assert surface_for("/ssf/streams") == "signals"
    assert surface_for("/something/else") == "other"
    assert surface_for("/v1/webhooksX") == "other"  # a prefix match needs a path boundary
    assert [status_class(c) for c in (200, 204, 301, 404, 429, 503)] == [
        "2xx",
        "2xx",
        "3xx",
        "4xx",
        "4xx",
        "5xx",
    ]
    now = datetime(2026, 10, 24, 12, 30, tzinfo=UTC)
    buf = UsageBuffer(limit=2)
    keys = [UsageKey("t", now, s, "c", "2xx") for s in ("a", "b", "c")]
    for k in keys:
        buf.add(k)
    buf.add(keys[0])
    assert len(buf) == 2  # the third distinct key is dropped, memory stays bounded
    assert buf.drain()[keys[0]] == 2 and len(buf) == 0


def test_rate_limiter_window_and_peek() -> None:
    rl = RateLimiter(3, window_s=60)
    assert [rl.check("t", float(i)).allowed for i in range(4)] == [True, True, True, False]
    refused = rl.check("t", 10.0)
    assert not refused.allowed and 1 <= refused.retry_after_s <= 60
    assert rl.peek("t", 10.0).used == 3  # a refused call is not counted
    assert rl.check("other", 10.0).allowed  # another tenant has its own window
    assert rl.check("t", 61.0).allowed  # the oldest hit has aged out
    assert RateLimiter(0).check("t", 0.0).allowed  # 0 = off


def test_usage_counts_and_tenant_rate_limit(rt_url: str, owner_url: str) -> None:
    a = onboard(rt_url, extra_actions=["metering.read"], extra_resources=["usage:*"])
    b = onboard(rt_url, extra_actions=["metering.read"], extra_resources=["usage:*"])
    app = create_app(
        Settings(
            database_url=rt_url,
            token_issuer=ISS,
            token_audience=AUD,
            public_base_url=AUD,
            environment="test",
            cell_id="uk-1",
            cell_uuid=CELL,
            api_rate_limit_per_minute=LIMIT,
        ),
        keyring=a.ring,
    )
    # both tenants sign with the same issuer key in this test
    b.ring = a.ring
    _ = (make_admin, ctx, engine, tenant_transaction, timedelta)

    with TestClient(app, base_url=ISS) as http:
        ha, hb = a.bearer(a.owner), b.bearer(b.owner)

        # --- unauthenticated calls cost no tenant anything and are not metered
        assert http.get("/v1/usage/limits").status_code == 401

        # --- tenant A spends its allowance; the first calls answer, then 429 with Retry-After
        statuses = [http.get("/v1/usage/limits", headers=ha).status_code for _ in range(LIMIT + 3)]
        assert statuses[:LIMIT] == [200] * LIMIT
        assert statuses[LIMIT:] == [429] * 3
        over = http.get("/v1/usage/limits", headers=ha)
        assert over.status_code == 429 and over.json()["error"]["code"] == "RATE_LIMITED"
        assert over.json()["error"]["retryable"] is True
        assert 1 <= int(over.headers["retry-after"]) <= 61

        # --- tenant B is not affected by A's burst
        ok = http.get("/v1/usage/limits", headers=hb)
        assert ok.status_code == 200
        lim = ok.json()
        assert (
            lim["enforced"] is True
            and lim["limit_per_minute"] == LIMIT
            and lim["used_this_minute"] == 1
        )

    # A is throttled, so read its counts back after the minute: use a fresh app/limiter (same database)
    app2 = create_app(
        Settings(
            database_url=rt_url,
            token_issuer=ISS,
            token_audience=AUD,
            public_base_url=AUD,
            environment="test",
            cell_id="uk-1",
            cell_uuid=CELL,
            api_rate_limit_per_minute=0,
        ),
        keyring=a.ring,
    )
    with TestClient(app2, base_url=ISS) as http2:
        assert http2.get("/v1/usage/limits", headers=ha).json()["enforced"] is False
        usage = http2.get("/v1/usage", headers=ha, params={"days": 1}).json()
        by = {(r["surface"], r["status_class"]): r["calls"] for r in usage["items"]}
        # app 1 recorded A's calls (8 ok + 4 throttled) and app 2 the two calls just made above
        assert by[("usage", "2xx")] >= LIMIT and by[("usage", "4xx")] >= 3
        assert usage["clients"] and usage["clients"][0]["errors"] >= 3
        # tenant B only sees its own counts
        ub = http2.get("/v1/usage", headers=hb, params={"days": 1}).json()
        assert sum(r["calls"] for r in ub["items"]) < sum(r["calls"] for r in usage["items"])
        assert http2.get("/v1/usage", headers=ha, params={"days": 90}).status_code == 422
