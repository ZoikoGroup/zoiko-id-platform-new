"""Partner entitlement rules (ZID-RCG-003 RCG-G03; CR-074): exact scope, dated, never inferred, never rewritten."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from zid_platform.modules.verification.domain.entitlement import (
    EntitlementError,
    Grant,
    Outcome,
    Scope,
    Status,
    changes,
    check_grant,
    decide,
)

NOW = datetime(2026, 11, 12, 12, 0, tzinfo=UTC)
SCOPE = Scope("acme-id", "DE", "INDIVIDUAL_VERIFICATION", "STANDARD")


def grant(**kw: object) -> Grant:
    base: dict[str, object] = {
        "grant_id": "g1",
        "scope": SCOPE,
        "status": Status.GRANTED,
        "effective_at": NOW - timedelta(days=10),
        "valid_from": NOW - timedelta(days=10),
        "valid_until": NOW + timedelta(days=300),
        "contract_ref": "CONTRACT-2026-17",
        "terms_ref": "TERMS-ACME-V3",
        "recorded_by": "vera",
        "recorded_at": NOW - timedelta(days=10),
    }
    base.update(kw)
    return Grant(**base)  # type: ignore[arg-type]


def code_of(g: Grant, **kw: object) -> str:
    with pytest.raises(EntitlementError) as e:
        check_grant(g, now=NOW, **kw)  # type: ignore[arg-type]
    return e.value.code


def test_a_granted_statement_inside_its_dates_entitles_the_exact_scope() -> None:
    d = decide([grant()], SCOPE, NOW)
    assert (d.outcome, d.reason, d.entitled) == (Outcome.ENTITLED, "entitled", True)


def test_no_statement_is_denied_never_a_pass() -> None:
    d = decide([], SCOPE, NOW)
    assert (d.outcome, d.reason) == (Outcome.DENIED, "no_entitlement")


@pytest.mark.parametrize(
    "other",
    [
        replace(SCOPE, provider="other-id"),
        replace(SCOPE, country="FR"),
        replace(SCOPE, capability="BUSINESS_VERIFICATION"),
        replace(SCOPE, purpose="MARKETING"),
    ],
)
def test_a_neighbouring_scope_is_not_covered(other: Scope) -> None:
    assert decide([grant()], other, NOW).reason == "no_entitlement"


def test_a_suspension_or_withdrawal_denies_from_the_time_it_takes_effect() -> None:
    suspended = grant(grant_id="g2", status=Status.SUSPENDED, effective_at=NOW - timedelta(hours=1))
    assert decide([grant(), suspended], SCOPE, NOW).reason == "suspended"
    withdrawn = grant(
        grant_id="g3", status=Status.WITHDRAWN, effective_at=NOW - timedelta(minutes=1)
    )
    assert decide([grant(), suspended, withdrawn], SCOPE, NOW).reason == "withdrawn"
    # asked about a moment before the suspension, the earlier statement still answers: history is not rewritten
    assert decide([grant(), suspended], SCOPE, NOW - timedelta(hours=2)).entitled


def test_a_restored_grant_entitles_again_but_only_from_its_own_time() -> None:
    suspended = grant(grant_id="g2", status=Status.SUSPENDED, effective_at=NOW - timedelta(days=2))
    restored = grant(grant_id="g3", effective_at=NOW - timedelta(hours=1))
    assert decide([grant(), suspended, restored], SCOPE, NOW).entitled
    assert (
        decide([grant(), suspended, restored], SCOPE, NOW - timedelta(days=1)).reason == "suspended"
    )


def test_dates_matter_not_yet_valid_and_expired_are_denied() -> None:
    future = grant(valid_from=NOW + timedelta(days=1), valid_until=NOW + timedelta(days=30))
    assert decide([future], SCOPE, NOW).reason == "not_yet_valid"
    ended = grant(valid_until=NOW - timedelta(seconds=1))
    assert decide([ended], SCOPE, NOW).reason == "expired"
    assert decide([grant(valid_until=None)], SCOPE, NOW + timedelta(days=9999)).entitled


def test_a_statement_needs_its_contract_and_terms() -> None:
    assert code_of(grant(contract_ref="x")) == "ENTITLEMENT_CONTRACT_REQUIRED"
    assert code_of(grant(terms_ref="")) == "ENTITLEMENT_TERMS_REQUIRED"
    assert code_of(grant(recorded_by=" ")) == "ENTITLEMENT_RECORDER_REQUIRED"


def test_a_statement_is_refused_from_the_future_out_of_order_with_bad_dates_or_a_bad_scope() -> (
    None
):
    assert code_of(grant(effective_at=NOW + timedelta(hours=1))) == "ENTITLEMENT_FROM_THE_FUTURE"
    earlier = grant(effective_at=NOW - timedelta(days=20))
    assert code_of(earlier, previous=[grant()]) == "ENTITLEMENT_OUT_OF_ORDER"
    assert code_of(grant(valid_until=NOW - timedelta(days=11))) == "ENTITLEMENT_DATES_INVALID"
    naive = grant(effective_at=datetime(2026, 1, 1))
    assert code_of(naive) == "ENTITLEMENT_TIME_INVALID"
    assert code_of(grant(scope=replace(SCOPE, country="de"))) == "ENTITLEMENT_COUNTRY_INVALID"
    assert code_of(grant(scope=replace(SCOPE, purpose="standard"))) == "ENTITLEMENT_SCOPE_INVALID"
    check_grant(grant(), now=NOW)  # a good one passes


def test_losing_an_entitlement_changes_exactly_the_scopes_it_covered() -> None:
    other = replace(SCOPE, country="FR")
    before = {
        s: decide([grant(), grant(scope=other, grant_id="o")], s, NOW) for s in (SCOPE, other)
    }
    suspended = grant(
        grant_id="g2", status=Status.SUSPENDED, effective_at=NOW - timedelta(minutes=1)
    )
    after = {
        SCOPE: decide([grant(), suspended], SCOPE, NOW),
        other: decide([grant(scope=other, grant_id="o")], other, NOW),
    }
    assert changes(before, after) == {SCOPE: (Outcome.ENTITLED, Outcome.DENIED)}
