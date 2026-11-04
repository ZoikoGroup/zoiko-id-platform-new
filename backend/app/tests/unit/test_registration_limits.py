"""The sign-up limiter stays bounded and still answers every caller the same way (ZID-UX-WF-013A; abuse resistance)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from zid_errors import ZidError
from zid_platform.modules.onboarding.api import registration as reg


def request(host: str) -> SimpleNamespace:
    return SimpleNamespace(client=SimpleNamespace(host=host))


@pytest.fixture(autouse=True)
def clean() -> None:
    reg._buckets.clear()


def test_one_client_is_limited_and_another_is_not() -> None:
    for _ in range(reg.LIMITS["resend"]):
        reg._limit(request("203.0.113.1"), "resend")  # type: ignore[arg-type]
    with pytest.raises(ZidError) as e:
        reg._limit(request("203.0.113.1"), "resend")  # type: ignore[arg-type]
    assert e.value.code == "RATE_LIMITED" and e.value.http_status == 429
    reg._limit(request("203.0.113.2"), "resend")  # type: ignore[arg-type]


def test_a_spray_of_addresses_cannot_grow_the_table_without_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(reg, "MAX_TRACKED_CLIENTS", 100)
    for i in range(1_000):
        reg._limit(request(f"198.51.{i // 250}.{i % 250}"), "intents")  # type: ignore[arg-type]
    assert len(reg._buckets["intents"]) <= 101  # one over is swept on the next call


def test_clients_whose_window_has_passed_are_forgotten_before_live_ones(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(reg, "MAX_TRACKED_CLIENTS", 3)
    table = reg._buckets["status"]
    table["old-a"].append(0.0)
    table["old-b"].append(0.0)
    table["live"].append(1_000_000.0)
    table["new"].append(1_000_000.0)
    reg._sweep(table, 1_000_000.0)
    assert set(table) == {"live", "new"}  # the two whose window passed are gone; the live ones stay
    table2 = reg._buckets["capabilities"]
    for i in range(6):
        table2[f"live-{i}"].append(1_000_000.0)
    reg._sweep(table2, 1_000_000.0)
    assert len(table2) == 3 and "live-5" in table2  # all live: the oldest go, the newest stay
