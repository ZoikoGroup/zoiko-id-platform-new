"""Tenant boundary proofs over HTTP and in the caches (ZID-VERIFY-MASTER-001 TEN-03, TEN-05, TEN-06).

Two onboarded tenants share one database. A caller of tenant A who names tenant B in every place a client
could (query, header, body) still reads and changes only A; the same object id in two tenants never shares a
cache entry; search can only be narrowed, never widened, by what the client sends.
"""

from __future__ import annotations

from datetime import UTC, datetime

from conftest import CELL
from fastapi.testclient import TestClient
from onboarding_fixture import AUD, ISS, Onboarded, onboard
from zid_platform.main import create_app
from zid_platform.modules.signals.application.state_cache import SecurityStateCache
from zid_platform.modules.signals.domain.rules import CurrentState, State
from zid_platform.settings import Settings


def app_for(o: Onboarded, rt_url: str) -> TestClient:
    app = create_app(
        Settings(
            database_url=rt_url,
            token_issuer=ISS,
            token_audience=AUD,
            public_base_url=AUD,
            environment="test",
            cell_id="uk-1",
            cell_uuid=CELL,
        ),
        keyring=o.ring,
    )
    return TestClient(app, base_url=ISS)


def test_client_supplied_tenant_and_cell_never_change_the_servers_scope(rt_url: str) -> None:
    """TEN-03 and TEN-06: tenant_id / cell_id in the query, headers and body are ignored."""
    extra = {"extra_actions": ["configuration.read", "configuration.manage"]}
    a = onboard(rt_url, extra_resources=["settings:*"], **extra)
    b = onboard(rt_url, extra_resources=["settings:*"], **extra)
    assert a.tenant != b.tenant
    spoof_headers = {
        "X-Tenant-Id": b.tenant,
        "X-Tenant": b.tenant,
        "X-Cell-Id": "other-cell",
        "Tenant-Id": b.tenant,
    }
    with app_for(a, rt_url) as ha, app_for(b, rt_url) as hb:
        before_b = hb.get("/v1/settings", headers=b.bearer(b.owner)).json()["areas"]

        # reading: the spoofed tenant changes nothing about whose settings come back
        plain = ha.get("/v1/settings", headers=a.bearer(a.owner))
        spoofed = ha.get(
            "/v1/settings",
            params={"tenant_id": b.tenant, "cell_id": "other-cell"},
            headers={**a.bearer(a.owner), **spoof_headers},
        )
        assert plain.status_code == spoofed.status_code == 200
        assert plain.json() == spoofed.json()

        # writing: A's change lands on A, with B named in the body, query and headers
        r = ha.put(
            "/v1/settings/BRANDING",
            params={"tenant_id": b.tenant},
            headers={**a.bearer(a.owner), **spoof_headers},
            json={
                "expected_revision": 0,
                "value": {"display_name": "Acme Global"},
                "tenant_id": b.tenant,
                "cell_id": "other-cell",
            },
        )
        assert r.status_code in (200, 422), r.text  # a strict body may refuse the extra fields
        after_a = ha.get("/v1/settings", headers=a.bearer(a.owner)).json()["areas"]
        after_b = hb.get("/v1/settings", headers=b.bearer(b.owner)).json()["areas"]
        assert after_b == before_b, "tenant B's settings moved because tenant A named it"
        if r.status_code == 200:
            assert after_a["BRANDING"]["revision"] == 1

        # a token for A is never accepted as B's: B's keys do not verify it, and A's scope never reaches B's rows
        assert hb.get("/v1/settings", headers=a.bearer(a.owner)).status_code in (401, 403)

        # search: a tenant filter in the query cannot widen the result beyond the caller's own tenant
        s = ha.get(
            "/v1/search",
            params={"q": "acme", "tenant_id": b.tenant},
            headers={**a.bearer(a.owner), **spoof_headers},
        )
        assert s.status_code in (200, 422)
        if s.status_code == 200:
            assert b.tenant not in s.text and b.owner not in s.text


def test_the_same_object_id_in_two_tenants_never_shares_a_cache_entry() -> None:
    """TEN-05: the security-state cache is keyed by tenant, so a colliding principal id stays separate."""
    now = datetime.now(UTC)
    cache = SecurityStateCache()
    suspended = CurrentState(State.SUSPENDED, 5, now, now)
    trusted = CurrentState(State.TRUSTED, 1, now, now)
    assert cache.put("tenant-a", "prn_1", suspended, now)
    assert (
        cache.get("tenant-b", "prn_1", risk_class=0, now=now) is None
    )  # B sees nothing of A's entry
    assert cache.put(
        "tenant-b", "prn_1", trusted, now
    )  # and B's older revision is not blocked by A's floor
    hit_a = cache.get("tenant-a", "prn_1", risk_class=0, now=now)
    hit_b = cache.get("tenant-b", "prn_1", risk_class=0, now=now)
    assert (
        hit_a is not None and hit_a.current is not None and hit_a.current.state is State.SUSPENDED
    )
    assert hit_b is not None and hit_b.current is not None and hit_b.current.state is State.TRUSTED
    cache.invalidate("tenant-a", "prn_1", 9)  # invalidating A leaves B alone
    assert cache.get("tenant-a", "prn_1", risk_class=0, now=now) is None
    assert cache.get("tenant-b", "prn_1", risk_class=0, now=now) is not None
