"""The credential status reliance cache stays inside its SLO and fails closed (ZID-VC-001; SIG-21)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from zid_platform.modules.credential.domain.reliance import RELIANCE_SLO, StatusReliance

T0 = datetime(2026, 1, 1, tzinfo=UTC)
URI = "https://issuer.example/status/abc-list-1"


def test_entry_is_reused_inside_the_slo_and_gone_after_it() -> None:
    """SIG-21: a cached list is served only until the profile SLO, then it must be fetched again."""
    c = StatusReliance()
    c.put(URI, {"status_list": {}}, T0, 300)
    assert c.get(URI, T0 + RELIANCE_SLO - timedelta(seconds=1)) is not None
    assert c.get(URI, T0 + RELIANCE_SLO) is None
    assert len(c) == 0


def test_the_list_own_ttl_can_shorten_but_never_extend_the_slo() -> None:
    c = StatusReliance()
    c.put(URI, {}, T0, 5)
    assert c.get(URI, T0 + timedelta(seconds=6)) is None
    c.put(URI, {}, T0, 86_400)
    assert c.get(URI, T0 + RELIANCE_SLO + timedelta(seconds=1)) is None


def test_a_revocation_made_here_invalidates_immediately() -> None:
    """SIG-21: revoking a credential drops its list from the cache at once, not at the SLO."""
    c = StatusReliance()
    c.put(URI, {}, T0, 300)
    c.put("https://issuer.example/status/other", {}, T0, 300)
    assert c.invalidate_containing("abc-list-1") == 1
    assert c.get(URI, T0) is None
    assert c.get("https://issuer.example/status/other", T0) is not None


def test_a_clock_that_goes_backwards_is_a_miss() -> None:
    c = StatusReliance()
    c.put(URI, {}, T0, 300)
    assert c.get(URI, T0 - timedelta(seconds=1)) is None


def test_cache_is_bounded() -> None:
    c = StatusReliance(max_entries=2)
    for i in range(5):
        c.put(f"{URI}{i}", {}, T0, 300)
    assert len(c) == 2
