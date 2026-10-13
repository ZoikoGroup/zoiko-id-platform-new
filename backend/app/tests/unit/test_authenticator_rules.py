"""Authenticator lifecycle and passkey assurance (ZID-AUTH-001 §3.3, §4.1)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from zid_platform.modules.authentication.domain.authenticator import (
    Authenticator,
    AuthenticatorStatus,
    AuthnError,
    can_remove,
    passkey_assurance,
    sign_count_anomaly,
    transition,
)

S = AuthenticatorStatus


def test_terminal_states_never_come_back() -> None:
    assert transition(S.ACTIVE, S.SUSPENDED) is S.SUSPENDED
    assert transition(S.SUSPENDED, S.REVOKED) is S.REVOKED
    for terminal in (S.REVOKED, S.LOST, S.REPLACED):
        for target in S:
            with pytest.raises(AuthnError):
                transition(terminal, target)
    with pytest.raises(AuthnError):
        transition(S.SUSPENDED, S.ACTIVE)


def test_sign_count_must_advance_when_used() -> None:
    assert not sign_count_anomaly(0, 0)  # authenticator without a counter
    assert not sign_count_anomaly(0, 1)
    assert not sign_count_anomaly(5, 6)
    assert sign_count_anomaly(5, 5) and sign_count_anomaly(5, 3) and sign_count_anomaly(5, 0)


def test_passkey_assurance() -> None:
    a = passkey_assurance(user_verified=True, backup_eligible=False, anomaly=False)
    assert (a.aal, a.amr) == (2, ("hwk", "user"))
    a = passkey_assurance(user_verified=True, backup_eligible=True, anomaly=False)
    assert (a.aal, a.amr) == (2, ("swk", "user"))
    assert passkey_assurance(user_verified=False, backup_eligible=True, anomaly=False).aal == 1
    assert passkey_assurance(user_verified=True, backup_eligible=False, anomaly=True).aal == 1


def test_last_strong_factor_is_replaced_through_recovery() -> None:
    a = Authenticator("a", "t", "p", "PASSKEY", S.ACTIVE, datetime.now(UTC))
    with pytest.raises(AuthnError) as e:
        can_remove(a, 0)
    assert e.value.code == "LAST_STRONG_AUTHENTICATOR"
    can_remove(a, 1)
    can_remove(Authenticator("b", "t", "p", "TOTP", S.ACTIVE, datetime.now(UTC)), 0)
    with pytest.raises(AuthnError):
        can_remove(Authenticator("c", "t", "p", "PASSKEY", S.REVOKED, datetime.now(UTC)), 3)
