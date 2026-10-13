"""Signal rule engine (ZID-SIGNALS-001 §2, §10-13, §22)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from zid_platform.modules.signals.domain.rules import (
    CAEP,
    RISC,
    RULES,
    CurrentState,
    SignalError,
    State,
    evaluate,
    normalize_event,
    pdp_state,
)

T = datetime(2026, 10, 13, 12, tzinfo=UTC)


def cur(state: State, at: datetime = T) -> CurrentState:
    return CurrentState(state, 4, at, at)


def test_normalization_boundary() -> None:
    assert normalize_event(CAEP + "credential-change", {"change_type": "revoke"}) == (
        "credential.revoked"
    )
    assert normalize_event(CAEP + "credential-change", {"change_type": "create"}) == (
        "credential.changed"
    )
    assert normalize_event(CAEP + "assurance-level-change", {"change_direction": "increase"}) == (
        "assurance.increased"
    )
    assert normalize_event(RISC + "account-disabled", {}) == "account.disabled"
    with pytest.raises(SignalError):
        normalize_event("https://evil.example/event", {})


def test_tightening_always_applies_loosening_needs_authority_and_recency() -> None:
    o = evaluate(
        RULES["credential.compromised"],
        cur(State.SUSPENDED, T + timedelta(hours=1)),
        effective_at=T,
        confidence="HIGH",
    )
    assert (o.status, o.new_state) == ("APPLIED", State.COMPROMISED)  # late but still true
    enable = RULES["account.enabled"]
    assert (
        evaluate(
            enable,
            cur(State.SUSPENDED),
            effective_at=T - timedelta(seconds=5),
            confidence="AUTHORITATIVE",
        ).status
        == "OUT_OF_ORDER"
    )  # SIG-06
    assert (
        evaluate(
            enable, cur(State.SUSPENDED), effective_at=T + timedelta(seconds=5), confidence="HIGH"
        ).status
        == "INFORMATIONAL"
    )  # not authoritative
    o = evaluate(
        enable,
        cur(State.SUSPENDED),
        effective_at=T + timedelta(seconds=5),
        confidence="AUTHORITATIVE",
    )
    assert (o.status, o.new_state) == (
        "APPLIED",
        State.REAUTH_REQUIRED,
    )  # never straight to TRUSTED
    weaker = evaluate(
        RULES["assurance.decreased"], cur(State.COMPROMISED), effective_at=T, confidence="HIGH"
    )
    assert weaker.new_state is State.COMPROMISED  # a weaker signal never relaxes the state


def test_terminal_states_are_terminal() -> None:
    for s in (State.REVOKED, State.PURGED):
        assert evaluate(
            RULES["account.disabled"], cur(s), effective_at=T, confidence="HIGH"
        ).status == ("OUT_OF_ORDER")


def test_pdp_view_and_fresh_authentication() -> None:
    assert pdp_state(None, auth_time=None) == "NORMAL"
    assert pdp_state(cur(State.COMPROMISED), auth_time=T + timedelta(hours=1)) == "COMPROMISED"
    assert pdp_state(cur(State.REAUTH_REQUIRED), auth_time=T - timedelta(seconds=1)) == (
        "REAUTH_REQUIRED"
    )
    assert pdp_state(cur(State.REAUTH_REQUIRED), auth_time=T + timedelta(seconds=1)) == "NORMAL"
    assert pdp_state(cur(State.SUSPENDED), auth_time=T + timedelta(days=1)) == "SUSPENDED"
