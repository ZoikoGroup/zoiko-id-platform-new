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


def test_allow_list_is_capped_for_the_browser_and_prefers_recent_strong_keys() -> None:
    from datetime import timedelta

    from zid_platform.modules.authentication.domain.authenticator import (
        MAX_ALLOWED_CREDENTIALS,
        credential_allow_list,
    )

    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    many = [
        Authenticator(
            f"a{i}",
            "t",
            "p",
            "PASSKEY",
            S.ACTIVE,
            t0 + timedelta(minutes=i),
            credential_id=bytes([i]),
        )
        for i in range(70)
    ]
    ids = credential_allow_list(many)
    assert len(ids) == MAX_ALLOWED_CREDENTIALS == 64
    assert (
        ids[0] == bytes([69]) and bytes([0]) not in ids
    )  # the newest are kept, the oldest dropped
    # a key used recently outranks newer registrations; weak or id-less authenticators never appear
    used = Authenticator(
        "u",
        "t",
        "p",
        "PASSKEY",
        S.ACTIVE,
        t0,
        credential_id=b"used",
        last_used_at=t0 + timedelta(days=1),
    )
    weak = Authenticator("w", "t", "p", "TOTP", S.ACTIVE, t0, credential_id=b"weak")
    bare = Authenticator("b", "t", "p", "PASSKEY", S.ACTIVE, t0)
    assert credential_allow_list([*many[:3], used, weak, bare]) == [
        b"used",
        bytes([2]),
        bytes([1]),
        bytes([0]),
    ]
    assert credential_allow_list([]) == []
