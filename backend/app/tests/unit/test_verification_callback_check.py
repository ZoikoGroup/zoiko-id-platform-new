"""A provider callback is stopped by its country check before anything is recorded, whether the check is synchronous or asynchronous
(ZID-RCG-003: the check may need the database, for the market's policy pack)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from zid_platform.modules.verification.application import service
from zid_platform.modules.verification.domain.verification import VerificationError
from zid_platform.modules.verification.infrastructure import verification_store as store

NOW = datetime(2026, 11, 10, tzinfo=UTC)


@pytest.fixture
def recorded(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    async def session_by_ref(conn: Any, adapter_id: str, ref: str) -> Any:
        return SimpleNamespace(jurisdiction="GB")

    async def record_callback(*args: Any, **kw: Any) -> str:
        calls.append("recorded")
        return "DUPLICATE"  # ends the ingest right after the recording

    monkeypatch.setattr(store, "session_by_ref", session_by_ref)
    monkeypatch.setattr(store, "record_callback", record_callback)
    return calls


def _ingest(check: Any) -> str:
    return asyncio.run(
        service._ingest(
            None,  # type: ignore[arg-type]
            tenant_id="t",
            adapter_id="a",
            adapter={},
            event_id="e",
            ref="r",
            result=None,  # type: ignore[arg-type]
            body=b"",
            country_check=check,
            now=NOW,
        )
    )


def test_an_asynchronous_check_that_refuses_stops_the_callback_before_anything_is_recorded(
    recorded: list[str],
) -> None:
    async def refuse(country: str) -> None:
        raise VerificationError("REGULATORY_POLICY_UNAVAILABLE", country)

    with pytest.raises(VerificationError) as e:
        _ingest(refuse)
    assert e.value.code == "REGULATORY_POLICY_UNAVAILABLE"
    assert recorded == []


def test_a_synchronous_check_still_refuses_exactly_as_before(recorded: list[str]) -> None:
    def refuse(country: str) -> None:
        raise VerificationError("COUNTRY_CAPABILITY_UNAVAILABLE", country)

    with pytest.raises(VerificationError):
        _ingest(refuse)
    assert recorded == []


def test_a_check_that_allows_lets_the_callback_be_recorded_whether_it_is_synchronous_or_not(
    recorded: list[str],
) -> None:
    async def allow(country: str) -> None:
        return None

    assert _ingest(allow) == "DUPLICATE"
    assert _ingest(lambda country: None) == "DUPLICATE"
    assert recorded == ["recorded", "recorded"]
