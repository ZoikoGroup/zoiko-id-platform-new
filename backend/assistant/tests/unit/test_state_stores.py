"""Ephemeral state: sessions (RET-02), rate limits (RET-12), runtime flags and the kill switch (INV-07, ADR-14)."""

from __future__ import annotations

import pytest
from assistant_helpers import make_settings
from zid_assistant.infrastructure.flags import FLAG_NAMES, Flags, FlagStore
from zid_assistant.infrastructure.ratelimit import RateLimiter
from zid_assistant.infrastructure.sessions import MemorySessionStore


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


@pytest.mark.anyio
async def test_session_handle_is_opaque_and_random() -> None:
    s = MemorySessionStore(1800, 7200)
    a, _ = await s.create("en-US")
    b, _ = await s.create("en-US")
    assert a != b and len(a) >= 40
    assert a not in str(vars(s)), (
        "the store is keyed by a hash of the handle, never the handle itself"
    )


@pytest.mark.anyio
async def test_idle_and_absolute_expiry() -> None:
    clock = Clock()
    s = MemorySessionStore(idle=100, absolute=250, clock=clock)
    h, _ = await s.create("en-US")
    clock.t += 99
    assert await s.touch(h) is not None  # activity extends the idle window...
    clock.t += 99
    assert await s.touch(h) is not None
    clock.t += 99  # ...but never beyond the absolute limit (297 > 250)
    assert await s.get(h) is None


@pytest.mark.anyio
async def test_idle_expiry_and_explicit_forget_clear_all_state() -> None:
    clock = Clock()
    s = MemorySessionStore(idle=100, absolute=1000, clock=clock)
    h, _ = await s.create("en-US")
    await s.put_message(h, "m1-aaaaaaaa", "d", "{}")
    await s.add_response(h, "rsp_" + "a" * 32)
    await s.delete(h)
    assert (
        await s.get(h) is None
        and await s.get_message(h, "m1-aaaaaaaa") is None
        and not await s.has_response(h, "rsp_" + "a" * 32)
    )
    h2, _ = await s.create("en-US")
    clock.t += 101
    assert await s.get(h2) is None and not s._m and not s._r


@pytest.mark.anyio
async def test_one_vote_per_response_and_unknown_handles_are_inert() -> None:
    s = MemorySessionStore(100, 1000)
    h, _ = await s.create("en-US")
    assert await s.mark_feedback(h, "rsp_x") is True and await s.mark_feedback(h, "rsp_x") is False
    assert await s.get("not-a-handle") is None and await s.touch("not-a-handle") is None
    await s.delete("not-a-handle")  # idempotent


def test_rate_limiter_window_and_per_key_isolation() -> None:
    clock = Clock()
    rl = RateLimiter(60, clock=clock)
    assert [rl.allow("m", "a", 3)[0] for _ in range(3)] == [True, True, True]
    ok, retry = rl.allow("m", "a", 3)
    assert not ok and 1 <= retry <= 60
    assert rl.allow("m", "b", 3)[0], "another key has its own budget"
    assert rl.allow("other", "a", 3)[0], "another bucket has its own budget"
    clock.t += 61
    assert rl.allow("m", "a", 3)[0], "the window slides"


def test_rate_limiter_keys_are_salted_and_not_stored_in_clear() -> None:
    rl = RateLimiter(60)
    rl.allow("m", "203.0.113.9", 5)
    rl2 = RateLimiter(60)
    rl2.allow("m", "203.0.113.9", 5)
    assert "203.0.113.9" not in repr(rl._buckets)
    assert set(rl._buckets["m"]) != set(rl2._buckets["m"]), (
        "a per-process salt means counters cannot become a tracking id"
    )


def test_rate_limiter_memory_is_bounded() -> None:
    clock = Clock()
    rl = RateLimiter(10, clock=clock)
    for i in range(50_500):
        rl.allow("m", f"k{i}", 5)
        if i % 1000 == 0:
            clock.t += 11
    assert len(rl._buckets["m"]) <= 50_500
    clock.t += 11
    rl.allow("m", "again", 5)
    assert len(rl._buckets["m"]) < 50_000  # stale keys were swept


@pytest.mark.anyio
async def test_every_flag_defaults_to_off() -> None:
    f = await FlagStore(make_settings(enabled=False)).get()
    assert not any(getattr(f, n) for n in FLAG_NAMES)
    assert (
        not f.chat_on and not f.ai_on and not f.sales_on and not f.support_on and not f.marketing_on
    )


@pytest.mark.anyio
async def test_kill_switch_overrides_everything_and_clears() -> None:
    store = FlagStore(
        make_settings(
            enabled=True, ai_answers=True, intake_sales=True, intake_support=True, marketing=True
        ),
        ttl=0,
    )
    f = await store.get()
    assert f.chat_on and f.ai_on and f.sales_on and f.support_on and f.marketing_on
    await store.set("kill_switch", True)
    f = await store.get()
    assert (
        not f.chat_on and not f.ai_on and not f.sales_on and not f.support_on and not f.marketing_on
    )
    await store.clear("kill_switch")
    assert (await store.get()).chat_on


@pytest.mark.anyio
async def test_each_switch_is_independent() -> None:
    store = FlagStore(make_settings(enabled=True, ai_answers=True, intake_sales=True), ttl=0)
    await store.set("ai_answers", False)
    f = await store.get()
    assert f.chat_on and not f.ai_on and f.sales_on, (
        "turning off the model leaves chat and intake running"
    )
    with pytest.raises(ValueError):
        await store.set("nonsense", True)


def test_marketing_needs_an_intake_purpose_and_chat() -> None:
    assert not Flags(True, False, False, False, True, False).marketing_on
    assert Flags(True, False, True, False, True, False).marketing_on
    assert not Flags(False, False, True, False, True, False).marketing_on
