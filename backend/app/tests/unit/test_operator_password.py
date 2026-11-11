"""Operator Console password sign-in: hashing, lockout and the start-up checks."""

from __future__ import annotations

import uuid

import pytest
from zid_platform.main import _operator_origins, _operator_password_logins
from zid_platform.modules.authentication.domain.operator_password import (
    LOCKOUT_SECONDS,
    MAX_FAILURES,
    Lockout,
    hash_password,
    operator_principal_id,
    valid_hash,
    verify_password,
)
from zid_platform.settings import Settings

PRINCIPAL = str(uuid.uuid4())
TENANT = str(uuid.uuid4())


def test_a_hash_verifies_only_its_own_password() -> None:
    stored = hash_password("correct horse battery")
    assert valid_hash(stored)
    assert verify_password("correct horse battery", stored)
    assert not verify_password("correct horse batterz", stored)
    assert hash_password("correct horse battery") != stored  # salted


def test_a_short_password_is_refused() -> None:
    with pytest.raises(ValueError, match="at least 12"):
        hash_password("short")


def test_a_malformed_hash_never_verifies() -> None:
    assert not valid_hash("plaintext")
    assert not verify_password("anything", "plaintext")


def test_five_failures_lock_the_email_until_the_window_passes() -> None:
    lock = Lockout()
    for _ in range(MAX_FAILURES - 1):
        lock.failed("op@x", 100.0)
    assert lock.locked_for("op@x", 100.0) == 0
    lock.failed("op@x", 100.0)
    assert lock.locked_for("op@x", 101.0) > 0
    assert lock.locked_for("op@x", 100.0 + LOCKOUT_SECONDS + 1) == 0
    lock.succeeded("op@x")
    assert lock.locked_for("op@x", 101.0) == 0


def _settings(environment: str, entry: dict[str, str]) -> Settings:
    return Settings(
        environment=environment,  # type: ignore[arg-type]
        operator_tenant_id=TENANT,
        operator_password_logins={"Op@Zoiko.Local": {"principal_id": PRINCIPAL, **entry}},
    )


def test_a_deployed_cell_takes_hashes_only() -> None:
    stored = hash_password("correct horse battery")
    logins = _operator_password_logins(_settings("production", {"password_hash": stored}))
    assert logins == {"op@zoiko.local": {"principal_id": PRINCIPAL, "password_hash": stored}}
    with pytest.raises(RuntimeError, match="password_hash is required"):
        _operator_password_logins(_settings("production", {"password": "correct horse battery"}))


def test_local_accepts_a_plain_password_and_hashes_it() -> None:
    logins = _operator_password_logins(_settings("local", {"password": "pw"}))
    assert verify_password("pw", logins["op@zoiko.local"]["password_hash"])


def test_logins_need_an_operator_tenant() -> None:
    s = Settings(
        environment="local",  # type: ignore[arg-type]
        operator_password_logins={"op@x": {"principal_id": PRINCIPAL, "password": "pw"}},
    )
    with pytest.raises(RuntimeError, match="ZID_OPERATOR_TENANT_ID"):
        _operator_password_logins(s)


def test_the_operator_from_two_environment_values_is_derived_and_hashed() -> None:
    s = Settings(
        environment="production",  # type: ignore[arg-type]
        operator_tenant_id=TENANT,
        operator_email="Op@Zoiko.Com",
        operator_password="test-fixture-pw-1",
    )
    logins = _operator_password_logins(s)
    entry = logins["op@zoiko.com"]
    assert entry["principal_id"] == operator_principal_id(TENANT, "op@zoiko.com", None)
    assert entry["principal_id"] == operator_principal_id(TENANT, "OP@zoiko.com ", None)  # stable
    assert verify_password("test-fixture-pw-1", entry["password_hash"])
    assert "test-fixture-pw-1" not in entry["password_hash"]


def test_a_configured_principal_id_wins_over_the_derived_one() -> None:
    assert operator_principal_id(TENANT, "op@x", PRINCIPAL) == PRINCIPAL


def test_the_operator_origin_includes_localhost_for_a_loopback_ip() -> None:
    s = Settings(operator_origin="http://127.0.0.1:3100")  # type: ignore[call-arg]
    assert _operator_origins(s) == {"http://127.0.0.1:3100", "http://localhost:3100"}
