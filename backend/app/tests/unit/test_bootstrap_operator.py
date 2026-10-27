"""First operator account from configuration: validation that needs no database."""

from __future__ import annotations

import pytest
from zid_platform.entrypoints.bootstrap_operator import (
    BootstrapError,
    enrollment_base,
    redirect_uris,
)
from zid_platform.settings import Settings


def settings(**kw: object) -> Settings:
    return Settings(environment="test", **kw)  # type: ignore[arg-type]


def test_redirect_defaults_to_the_origins_callback() -> None:
    s = settings(operator_origin="https://operator.example.com/")
    assert redirect_uris(s) == ("https://operator.example.com/auth/callback",)


def test_redirect_must_be_https_or_loopback_ip() -> None:
    assert redirect_uris(settings(operator_redirect_uris=("http://127.0.0.1:3100/auth/callback",)))
    for bad in (
        "http://operator.example.com/auth/callback",
        "https://operator.example.com/cb#x",
        "javascript:alert(1)",
    ):
        with pytest.raises(BootstrapError):
            redirect_uris(settings(operator_redirect_uris=(bad,)))
    with pytest.raises(BootstrapError, match="ZID_OPERATOR_ORIGIN"):
        redirect_uris(settings())


def test_enrollment_link_is_served_by_the_console_itself() -> None:
    s = settings(
        operator_origin="https://operator.example.com",
        webauthn_origins=["https://id.example.com", "https://operator.example.com"],
    )
    assert enrollment_base(s) == "https://operator.example.com"


def test_loopback_ip_origin_becomes_localhost_for_the_passkey_link() -> None:
    s = settings(
        operator_origin="http://127.0.0.1:3100", webauthn_origins=["http://localhost:3100"]
    )
    assert enrollment_base(s) == "http://localhost:3100"


def test_enrollment_needs_the_console_origin_in_the_passkey_origins() -> None:
    with pytest.raises(BootstrapError, match="ZID_WEBAUTHN_ORIGINS"):
        enrollment_base(
            settings(
                operator_origin="https://operator.example.com",
                webauthn_origins=["https://id.example.com"],
            )
        )
    with pytest.raises(BootstrapError, match="ZID_OPERATOR_ORIGIN"):
        enrollment_base(settings())
