"""The entitlement decision (ZID-COM-001 §6, §29; CR-024). Pure rules, no database."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from zid_platform.modules.commercial.domain.catalogue import (
    ENTITLEMENT_UNAVAILABLE,
    NOT_IN_PLAN,
    NOT_RELEASED,
    QUOTA_EXHAUSTED,
    SUSPENDED,
    UNKNOWN_CAPABILITY,
    Override,
    PlanCapability,
    decide,
)

NOW = datetime(2026, 10, 24, 12, tzinfo=UTC)


def rule(mode: str, *, count: int | None = None, released: bool = True) -> PlanCapability:
    return PlanCapability("saml_sso", mode, None, count, released)


def ask(
    mode: str,
    *,
    count: int | None = None,
    released: bool = True,
    overrides: list[Override] | None = None,
    used: int = 0,
    quantity: int = 1,
    enforcement: str = "enforce",
    state: str = "ACTIVE",
):  # type: ignore[no-untyped-def]
    return decide(
        "saml_sso",
        plan_code="BUSINESS",
        rule=rule(mode, count=count, released=released),
        overrides=overrides or [],
        used=used,
        quantity=quantity,
        now=NOW,
        enforcement=enforcement,  # type: ignore[arg-type]
        state=state,
    )


def test_included_counted_capability_allows_up_to_its_cap() -> None:
    assert ask("INCLUDED", count=1, used=0).allowed
    refused = ask("INCLUDED", count=1, used=1)
    assert not refused.allowed and refused.reason == QUOTA_EXHAUSTED and refused.limit == 1
    assert ask("INCLUDED", count=3, used=2).allowed and not ask("INCLUDED", count=3, used=3).allowed


def test_a_quantity_over_the_remaining_room_is_refused() -> None:
    assert not ask("INCLUDED", count=3, used=1, quantity=3).allowed
    assert ask("INCLUDED", count=3, used=1, quantity=2).allowed


@pytest.mark.parametrize("mode", ["ADD_ON", "CONTRACT", "QUALIFIED", "NONE"])
def test_paid_modes_need_an_override(mode: str) -> None:
    assert ask(mode).reason == NOT_IN_PLAN
    grant = Override("saml_sso", "ADD_ON", None)
    assert ask(mode, overrides=[grant]).allowed


def test_extra_count_adds_to_the_plan_cap() -> None:
    extra = Override("saml_sso", None, 2)
    assert ask("ADD_ON", overrides=[extra], used=1).allowed  # 0 included + 2 extra
    assert not ask("ADD_ON", overrides=[extra], used=2).allowed
    assert ask("INCLUDED", count=1, overrides=[extra], used=2).allowed  # 1 + 2 = 3
    assert not ask("INCLUDED", count=1, overrides=[extra], used=3).allowed


def test_an_unreleased_capability_is_never_granted_even_with_an_override() -> None:
    grant = Override("saml_sso", "CONTRACT", 5)
    r = ask("INCLUDED", released=False, overrides=[grant])
    assert not r.allowed and r.reason == NOT_RELEASED


def test_expired_and_revoked_overrides_grant_nothing() -> None:
    expired = Override("saml_sso", "ADD_ON", None, expires_at=NOW - timedelta(seconds=1))
    revoked = Override("saml_sso", "ADD_ON", None, revoked=True)
    other = Override("scim", "ADD_ON", None)
    for o in (expired, revoked, other):
        assert ask("ADD_ON", overrides=[o]).reason == NOT_IN_PLAN
    assert ask(
        "ADD_ON", overrides=[Override("saml_sso", "ADD_ON", None, NOW + timedelta(days=1))]
    ).allowed


def test_metered_and_included_without_a_count_are_open_and_sandbox_is_marked() -> None:
    assert ask("METERED").allowed and ask("INCLUDED").allowed
    s = ask("SANDBOX")
    assert s.allowed and s.sandbox_only


def test_a_suspended_entitlement_and_an_unknown_capability_are_refused() -> None:
    assert ask("INCLUDED", state="SUSPENDED").reason == SUSPENDED
    r = decide(
        "nope",
        plan_code="BUSINESS",
        rule=None,
        overrides=[],
        used=None,
        quantity=1,
        now=NOW,
        enforcement="enforce",
    )
    assert not r.allowed and r.reason == UNKNOWN_CAPABILITY


def test_shadow_reports_a_denial_without_blocking_and_off_ignores_it() -> None:
    shadow = ask("NONE", enforcement="shadow")
    assert shadow.allowed and not shadow.would_allow and shadow.shadow_denied
    assert shadow.reason == NOT_IN_PLAN
    off = ask("NONE", enforcement="off")
    assert off.allowed and not off.would_allow and not off.shadow_denied
    on = ask("NONE", enforcement="enforce")
    assert not on.allowed and not on.would_allow and not on.shadow_denied


def test_an_allowed_decision_is_identical_in_every_mode() -> None:
    results = {
        m: ask("INCLUDED", count=2, used=1, enforcement=m) for m in ("off", "shadow", "enforce")
    }
    assert all(r.allowed and r.would_allow and r.reason is None for r in results.values())


def test_reason_codes_are_stable() -> None:
    assert (ENTITLEMENT_UNAVAILABLE, NOT_IN_PLAN, NOT_RELEASED, QUOTA_EXHAUSTED) == (
        "ENTITLEMENT_UNAVAILABLE",
        "NOT_IN_PLAN",
        "NOT_RELEASED",
        "QUOTA_EXHAUSTED",
    )
