"""Revision-aware security-state cache bounded by risk class (FR-SIG-006)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from zid_platform.modules.signals.application.state_cache import SecurityStateCache, security_view
from zid_platform.modules.signals.domain.rules import (
    STATE_CACHE_TTL,
    CurrentState,
    State,
    cache_fresh,
)

T0 = datetime(2026, 10, 13, 12, tzinfo=UTC)


def state(s: State, revision: int) -> CurrentState:
    return CurrentState(s, revision, T0, T0)


def test_ttl_by_risk_class() -> None:
    assert STATE_CACHE_TTL[2] == STATE_CACHE_TTL[3] == timedelta(0)
    assert cache_fresh(T0, now=T0 + timedelta(seconds=4), risk_class=1)
    assert not cache_fresh(T0, now=T0 + timedelta(seconds=5), risk_class=1)
    assert cache_fresh(T0, now=T0 + timedelta(seconds=29), risk_class=0)
    for rc in (2, 3, 9):
        assert not cache_fresh(T0, now=T0, risk_class=rc)
    assert not cache_fresh(T0, now=T0 - timedelta(seconds=1), risk_class=0)  # clock went back


def test_never_regresses_and_invalidation_is_immediate() -> None:
    c = SecurityStateCache()
    assert c.put("t", "p", state(State.SUSPENDED, 3), T0)
    assert not c.put("t", "p", state(State.TRUSTED, 2), T0)  # older read is not cached
    hit = c.get("t", "p", risk_class=1, now=T0)
    assert hit is not None and hit.revision == 3
    c.invalidate("t", "p", 4)
    assert c.get("t", "p", risk_class=0, now=T0) is None
    assert not c.put("t", "p", state(State.SUSPENDED, 3), T0)  # floor holds
    assert c.put("t", "p", state(State.TRUSTED, 4), T0)
    c.invalidate("t", "p", 2)  # an older write notice changes nothing
    assert c.get("t", "p", risk_class=0, now=T0) is not None


def test_bounded() -> None:
    c = SecurityStateCache(max_entries=2)
    for p in ("a", "b", "c"):
        c.put("t", p, None, T0)
    assert c.get("t", "a", risk_class=0, now=T0) is None
    assert c.get("t", "c", risk_class=0, now=T0) is not None


class FakeConn:
    def __init__(self, current: CurrentState | None) -> None:
        self.current, self.reads = current, 0


@pytest.fixture
def fake_store(monkeypatch: pytest.MonkeyPatch) -> Any:
    from zid_platform.modules.signals.application import state_cache

    async def read_state(conn: FakeConn, subject_type: str, subject_id: str) -> Any:
        conn.reads += 1
        return conn.current

    monkeypatch.setattr(state_cache.store, "read_state", read_state)


def test_r2_r3_always_read_committed_state(fake_store: None) -> None:
    c = SecurityStateCache()
    conn = FakeConn(state(State.TRUSTED, 1))

    def view(rc: int, at: datetime, connection: Any = conn) -> Any:
        return asyncio.run(
            security_view(
                connection,
                tenant_id="t",
                principal_id="p",
                auth_time=None,
                risk_class=rc,
                now=at,
                cache=c,
            )
        )

    assert view(1, T0).cached is False
    conn.current = state(State.SUSPENDED, 2)  # committed by another process: no invalidation
    v = view(1, T0 + timedelta(seconds=1))
    assert v.cached and v.state == "NORMAL"  # within R1's bound
    v = view(3, T0 + timedelta(seconds=1))
    assert not v.cached and v.state == "SUSPENDED" and v.revision == 2  # R3 reads through
    assert view(1, T0 + timedelta(seconds=1)).state == "SUSPENDED"  # and refreshed the cache
    with pytest.raises(LookupError):
        view(2, T0, None)  # no cached path exists for R2/R3
