"""Exact-scope suspension (ZID-RCG-003 RCG-G08, section 08.2; T08; CR-078): the kill switch stops exactly its scope, is versioned, lifted only by
another person, and never rewrites history."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from zid_platform.modules.verification.domain import suspension as sp

NOW = datetime(2026, 11, 12, 12, tzinfo=UTC)
RTR_GB = sp.Scope("acme", service_id="RTR", country="GB")


def suspension(**over: Any) -> sp.Suspension:
    base = sp.Suspension(
        suspension_id="s1",
        version=1,
        scope=RTR_GB,
        trigger=sp.Trigger.CERTIFICATION_WITHDRAWN,
        status=sp.Status.ACTIVE,
        reason="the register shows the certificate withdrawn",
        evidence_ref="INC-42",
        effective_at=NOW - timedelta(hours=1),
        recorded_by="ana",
        recorded_at=NOW - timedelta(hours=1),
    )
    return replace(base, **over)


def lift_of(s: sp.Suspension, **over: Any) -> sp.Suspension:
    values: dict[str, Any] = {
        "version": s.version + 1,
        "status": sp.Status.LIFTED,
        "reason": "the register shows the certificate restored",
        "evidence_ref": "INC-43",
        "effective_at": NOW,
        "recorded_by": "ben",
        "recorded_at": NOW,
    }
    return replace(s, **(values | over))


def code(fn: Any, *a: Any, **k: Any) -> str:
    with pytest.raises(sp.SuspensionError) as e:
        fn(*a, **k)
    return e.value.code


# ---- exact scope


def test_a_suspension_stops_exactly_the_calls_in_its_scope() -> None:
    assert sp.matches(
        RTR_GB, sp.Invocation("acme", service_id="RTR", country="GB", purpose="STANDARD")
    )


@pytest.mark.parametrize(
    "call",
    [
        sp.Invocation("acme", service_id="IDV", country="GB"),  # the provider's unrelated service
        sp.Invocation("acme", service_id="RTR", country="IE"),  # the same service elsewhere
        sp.Invocation("other", service_id="RTR", country="GB"),  # another provider
        sp.Invocation(
            "acme", service_id="RTR", country=None
        ),  # a call that cannot say its country is not stopped by a guess
    ],
)
def test_unrelated_services_places_and_providers_are_not_disabled(call: sp.Invocation) -> None:
    assert not sp.matches(RTR_GB, call)


def test_a_provider_wide_freeze_matches_every_call_of_that_provider() -> None:
    assert sp.matches(sp.Scope("acme"), sp.Invocation("acme", service_id="anything", country="FR"))


def test_a_whole_provider_is_frozen_only_for_a_breach() -> None:
    wide = suspension(scope=sp.Scope("acme"))
    assert code(sp.check_new, wide, now=NOW) == "SUSPENSION_TOO_BROAD"
    sp.check_new(replace(wide, trigger=sp.Trigger.PROVIDER_BREACH), now=NOW)


# ---- applying


def test_a_good_suspension_passes_and_needs_a_reason_evidence_and_a_time_now() -> None:
    sp.check_new(suspension(), now=NOW)
    assert code(sp.check_new, suspension(reason="short"), now=NOW) == "SUSPENSION_REASON_REQUIRED"
    assert (
        code(sp.check_new, suspension(evidence_ref=""), now=NOW) == "SUSPENSION_EVIDENCE_REQUIRED"
    )
    assert (
        code(sp.check_new, suspension(effective_at=NOW + timedelta(hours=1)), now=NOW)
        == "SUSPENSION_FROM_THE_FUTURE"
    )
    assert (
        code(sp.check_new, suspension(effective_at=datetime(2026, 1, 1)), now=NOW)
        == "SUSPENSION_TIME_INVALID"
    )
    assert code(sp.check_new, suspension(version=2), now=NOW) == "SUSPENSION_OUT_OF_ORDER"
    assert (
        code(sp.check_new, suspension(suspension_id="Bad ID"), now=NOW) == "SUSPENSION_ID_INVALID"
    )
    assert (
        code(sp.check_new, suspension(scope=sp.Scope(" ", country="GB")), now=NOW)
        == "SUSPENSION_PROVIDER_REQUIRED"
    )


# ---- lifting (the rollback)


def test_a_lift_is_the_next_version_by_another_person_with_the_same_scope() -> None:
    s = suspension()
    sp.check_lift(lift_of(s), previous=[s], now=NOW)
    assert (
        code(sp.check_lift, lift_of(s, recorded_by="ana"), previous=[s], now=NOW)
        == "SUSPENSION_LIFTED_BY_APPLIER"
    )
    assert (
        code(
            sp.check_lift,
            lift_of(s, scope=sp.Scope("acme", service_id="IDV")),
            previous=[s],
            now=NOW,
        )
        == "SUSPENSION_SCOPE_CHANGED"
    )
    assert code(sp.check_lift, lift_of(s), previous=[], now=NOW) == "SUSPENSION_NOT_FOUND"
    lifted = lift_of(s)
    assert (
        code(sp.check_lift, lift_of(lifted, recorded_by="cara"), previous=[s, lifted], now=NOW)
        == "SUSPENSION_NOT_ACTIVE"
    )
    early = lift_of(s, effective_at=s.effective_at - timedelta(minutes=1))
    assert code(sp.check_lift, early, previous=[s], now=NOW) == "SUSPENSION_OUT_OF_ORDER"


# ---- in force over time (T08: history is not rewritten)


def test_t08_the_answer_for_a_past_moment_is_the_version_in_force_then() -> None:
    s = suspension(effective_at=NOW - timedelta(hours=2))
    lifted = lift_of(s, effective_at=NOW - timedelta(hours=1))
    history = [s, lifted]
    call = sp.Invocation("acme", service_id="RTR", country="GB")
    assert sp.blocking(history, call, NOW - timedelta(hours=3)) == []  # before the suspension
    assert [x.suspension_id for x in sp.blocking(history, call, NOW - timedelta(minutes=90))] == [
        "s1"
    ]  # while it was in force
    assert sp.blocking(history, call, NOW) == []  # after the lift


def test_several_suspensions_are_each_judged_on_their_own() -> None:
    a = suspension()
    b = suspension(suspension_id="s2", scope=sp.Scope("acme", service_id="IDV"))
    history = [a, b, lift_of(a)]
    assert [s.suspension_id for s in sp.in_force(history, NOW)] == ["s2"]


# ---- impact


def test_impact_lists_exactly_what_the_scope_touches() -> None:
    gb_rtr = sp.Invocation("acme", service_id="RTR", country="GB")
    gb_idv = sp.Invocation("acme", service_id="IDV", country="GB")
    i = sp.impact(
        RTR_GB,
        markets=[("gb-release:v1", gb_rtr), ("gb-idv-release:v1", gb_idv)],
        entitlements=[("acme/GB/RTR/STANDARD", gb_rtr)],
        certifications=[("acme/UK/GB/RTR", gb_rtr), ("acme/UK/GB/IDV", gb_idv)],
        claims=[("gb-claim:v1", [gb_idv, gb_rtr]), ("idv-claim:v1", [gb_idv])],
    )
    assert i.markets == ("gb-release:v1",) and i.entitlements == ("acme/GB/RTR/STANDARD",)
    assert i.certifications == ("acme/UK/GB/RTR",) and i.claims == ("gb-claim:v1",)
    assert not i.empty and sp.impact(RTR_GB).empty
