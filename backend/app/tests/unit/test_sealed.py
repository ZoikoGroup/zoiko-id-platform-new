"""A sealed value the server hands to a client and takes back (CR-108): unreadable, unchangeable, bound, expiring."""

from __future__ import annotations

import asyncio
import base64
import dataclasses
import json
from collections.abc import Iterator

import pytest
from zid_platform.modules.evidence.application import sealed
from zid_platform.modules.evidence.application.runtime import configure_runtime, development_runtime

BIND = {"session": "S1", "principal": "P1", "client": "C1"}


@pytest.fixture(autouse=True)
def runtime() -> Iterator[None]:
    configure_runtime(development_runtime("uk-sealed"))
    yield
    configure_runtime(None)


def run(coro):
    return asyncio.run(coro)


def tamper(token: str, field: str) -> str:
    box = json.loads(base64.urlsafe_b64decode(token))
    raw = bytearray(base64.urlsafe_b64decode(box[field]))
    raw[0] ^= 1
    box[field] = base64.urlsafe_b64encode(bytes(raw)).decode()
    return base64.urlsafe_b64encode(json.dumps(box).encode()).decode()


def test_a_sealed_value_comes_back_as_it_went_and_the_client_cannot_read_it() -> None:
    token = run(
        sealed.seal("T1", "demo", BIND, {"secret": "hunter2-marker", "n": 3}, ttl_seconds=60)
    )
    assert (
        "hunter2-marker" not in token
        and "hunter2-marker" not in base64.urlsafe_b64decode(token).decode()
    )
    assert run(sealed.open_sealed("T1", "demo", BIND, token)) == {
        "secret": "hunter2-marker",
        "n": 3,
    }
    assert token != run(
        sealed.seal("T1", "demo", BIND, {"secret": "hunter2-marker", "n": 3}, ttl_seconds=60)
    )  # a fresh key and nonce every time


def test_it_opens_only_for_the_tenant_purpose_and_binding_it_was_sealed_for() -> None:
    token = run(sealed.seal("T1", "demo", BIND, {"a": 1}, ttl_seconds=60))
    for tenant, purpose, binding in (
        ("T2", "demo", BIND),
        ("T1", "other-purpose", BIND),
        ("T1", "demo", {**BIND, "session": "S2"}),
        ("T1", "demo", {**BIND, "principal": "P2"}),
        ("T1", "demo", {**BIND, "client": "C2"}),
        ("T1", "demo", {"session": "S1"}),
    ):
        with pytest.raises(sealed.SealError) as e:
            run(sealed.open_sealed(tenant, purpose, binding, token))
        assert e.value.code == "SEALED_INVALID", (tenant, purpose, binding)


def test_a_changed_or_made_up_value_is_refused_with_one_answer() -> None:
    token = run(sealed.seal("T1", "demo", BIND, {"a": 1}, ttl_seconds=60))
    for bad in (
        tamper(token, "c"),
        tamper(token, "w"),
        tamper(token, "n"),
        "",
        "x",
        "%%%",
        token[:-4],
        "A" * 400,
        "A" * (sealed.MAX_SEALED + 1),
    ):
        with pytest.raises(sealed.SealError) as e:
            run(sealed.open_sealed("T1", "demo", BIND, bad))
        assert e.value.code == "SEALED_INVALID"


def test_it_stops_opening_when_its_time_is_up() -> None:
    import time as real

    token = run(sealed.seal("T1", "demo", BIND, {"a": 1}, ttl_seconds=5))
    assert run(sealed.open_sealed("T1", "demo", BIND, token)) == {"a": 1}
    orig = real.time
    try:
        real.time = lambda: orig() + 6  # type: ignore[assignment]
        with pytest.raises(sealed.SealError) as e:
            run(sealed.open_sealed("T1", "demo", BIND, token))
        assert e.value.code == "SEALED_EXPIRED"
    finally:
        real.time = orig  # type: ignore[assignment]


def test_another_process_with_the_same_keys_opens_it_and_one_with_other_keys_cannot() -> None:
    token = run(sealed.seal("T1", "demo", BIND, {"a": 1}, ttl_seconds=60))
    same_keys = dataclasses.replace(
        sealed.current_runtime()
    )  # another process of the same deployment: the same key provider (a shared KMS)
    configure_runtime(same_keys)
    assert run(sealed.open_sealed("T1", "demo", BIND, token)) == {"a": 1}
    configure_runtime(development_runtime("uk-sealed"))  # a different deployment: its own keys
    with pytest.raises(sealed.SealError) as e:
        run(sealed.open_sealed("T1", "demo", BIND, token))
    assert e.value.code == "SEALED_INVALID"


def test_a_value_too_large_to_hand_to_a_client_is_refused_up_front() -> None:
    with pytest.raises(sealed.SealError) as e:
        run(sealed.seal("T1", "demo", BIND, {"blob": "x" * 400_000}, ttl_seconds=60))
    assert e.value.code == "SEALED_TOO_LARGE"
