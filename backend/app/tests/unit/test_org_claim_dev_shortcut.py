"""The LOCAL/TEST-ONLY company claim shortcut (ZID_ORG_CLAIM_DEV_AUTOVERIFY).

With it on, a claim reads as VERIFIED as soon as the claimant's own identity check is complete: no register lookup, screening, authority check or review.
It is off by default, refused outside local and test, never applies to a closed claim or to a claimant whose identity is not verified, and never announces
checks that did not run.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from zid_platform.modules.onboarding.application import claims
from zid_platform.modules.organization.domain.kyb import KybStatus

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _reset() -> Any:
    claims.configure_dev_shortcut(False, "test")
    yield
    claims.configure_dev_shortcut(False, "test")


def run_status(
    monkeypatch: pytest.MonkeyPatch, *, identity: bool, closed: str | None = None
) -> dict[str, Any]:
    case = SimpleNamespace(
        status=KybStatus.UNAVAILABLE, requested_by="person-1", closed_reason=closed, jurisdiction="GB",
        registration_number="00123456", submitted_at=None, closed_at=None, review_reason=None, reasons=(), expiry_at=None,
    )  # fmt: skip

    async def get_case(*_a: Any, **_k: Any) -> Any:
        return case

    async def no_screening(*_a: Any, **_k: Any) -> list[Any]:
        return []

    async def assurance(*_a: Any, **_k: Any) -> bool:
        return identity

    monkeypatch.setattr(claims, "kyb_get_case", get_case)
    monkeypatch.setattr(claims, "case_for_kyb", no_screening)
    monkeypatch.setattr(claims, "person_assurance_current", assurance)
    return asyncio.run(claims.status(None, "t", "c", NOW))  # type: ignore[arg-type]


def test_it_is_off_by_default_and_a_complete_identity_alone_verifies_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = run_status(monkeypatch, identity=True)
    assert seen["outcome"] != "VERIFIED" and seen["development_shortcut"] is False
    assert (
        seen["checks"]["company"] != "COMPLETED"
    )  # the register check did not run, so it is not complete


def test_with_it_on_the_claimants_identity_is_the_only_check_that_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claims.configure_dev_shortcut(True, "local")
    seen = run_status(monkeypatch, identity=True)
    assert seen["outcome"] == "VERIFIED" and seen["development_shortcut"] is True
    assert set(seen["checks"].values()) == {"COMPLETED"}


def test_without_a_verified_identity_or_on_a_closed_claim_the_shortcut_does_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claims.configure_dev_shortcut(True, "test")
    pending = run_status(monkeypatch, identity=False)
    assert pending["outcome"] != "VERIFIED" and pending["development_shortcut"] is False
    closed = run_status(monkeypatch, identity=True, closed="WITHDRAWN")
    assert closed["outcome"] == "WITHDRAWN" and closed["development_shortcut"] is False


def test_it_cannot_be_turned_on_outside_local_and_test() -> None:
    for environment in ("production", "staging", "sandbox", "development", ""):
        with pytest.raises(RuntimeError):
            claims.configure_dev_shortcut(True, environment)
    assert claims.dev_shortcut_enabled() is False
    claims.configure_dev_shortcut(False, "production")  # turning it off is always allowed


def test_checks_that_never_ran_are_never_announced() -> None:
    seen = {"development_shortcut": True, "outcome": "VERIFIED", "checks": {"company": "COMPLETED"}}
    assert asyncio.run(claims.record_outcome(None, "t", "c", seen, NOW)) == []  # type: ignore[arg-type]  # no connection is even touched
