"""Forced re-authentication at the identity provider (ZID-ENG-DEC-009 T07; slice L-9, CR-046 addendum): the request asks for it (OIDC `max_age=0` and
`prompt=login`, SAML `ForceAuthn`), and the callback refuses a response that does not show the person authenticated after the request was made."""

from __future__ import annotations

import asyncio
import base64
import zlib
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from lxml import etree
from zid_platform.modules.federation.application import oidc_login, saml_login
from zid_platform.modules.federation.domain.connection import Connection, FederationError
from zid_platform.modules.federation.infrastructure import connection_store as store

NOW = datetime(2026, 11, 4, 10, 0, tzinfo=UTC)


def _conn(protocol: str) -> Connection:
    return Connection(
        connection_id="c",
        tenant_id="00000000-0000-0000-0000-000000000001",
        protocol=protocol,
        display_name="x",
        issuer="https://idp.example.com",
        status="ACTIVE",
        config_revision=1,
        metadata_expires_at=NOW + timedelta(days=1),
        acr_levels={},
        trust_amr=True,
        link_policy="EXISTING_ONLY",
        client_id="cid",
        authorization_endpoint="https://idp.example.com/authorize",
        scopes=("openid",),
        sso_url="https://idp.example.com/sso",
        sp_entity_id="https://sp.example.com",
    )


class _Settings:
    callback = "https://id.zoiko.test/federation/callback"
    acs = "https://id.zoiko.test/federation/saml/acs"


def _record(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    seen: list[dict[str, Any]] = []

    async def start_login(conn: Any, **kw: Any) -> str:
        seen.append(kw)
        return "state-1"

    monkeypatch.setattr(store, "start_login", start_login)
    return seen


def test_an_oidc_request_that_forces_reauthentication_says_so(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _record(monkeypatch)
    url = asyncio.run(
        oidc_login.begin(
            object(),
            _Settings(),
            _conn("OIDC"),
            client_id=None,
            return_to=None,
            link_principal_id=None,
            now=NOW,
            force_reauth=True,
        )
    )  # type: ignore[arg-type]
    q = parse_qs(urlparse(url).query)
    assert q["max_age"] == ["0"] and q["prompt"] == ["login"]
    assert seen[0]["force_reauth"] is True


def test_an_ordinary_oidc_request_does_not(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _record(monkeypatch)
    url = asyncio.run(
        oidc_login.begin(
            object(),
            _Settings(),
            _conn("OIDC"),
            client_id=None,
            return_to=None,
            link_principal_id=None,
            now=NOW,
        )
    )  # type: ignore[arg-type]
    q = parse_qs(urlparse(url).query)
    assert "max_age" not in q and "prompt" not in q
    assert seen[0]["force_reauth"] is False


def _saml_request(url: str) -> Any:
    raw = base64.b64decode(parse_qs(urlparse(url).query)["SAMLRequest"][0])
    return etree.fromstring(zlib.decompress(raw, -15))


def test_a_saml_request_that_forces_reauthentication_carries_force_authn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _record(monkeypatch)
    settings = type("S", (), {"callback": _Settings.callback, "acs": _Settings.acs})()
    monkeypatch.setattr(saml_login, "acs_url", lambda s: _Settings.acs)
    on = asyncio.run(
        saml_login.begin_saml(
            object(),
            settings,
            _conn("SAML"),
            client_id=None,
            return_to=None,
            link_principal_id=None,
            now=NOW,
            force_reauth=True,
        )
    )  # type: ignore[arg-type]
    off = asyncio.run(
        saml_login.begin_saml(
            object(),
            settings,
            _conn("SAML"),
            client_id=None,
            return_to=None,
            link_principal_id=None,
            now=NOW,
        )
    )  # type: ignore[arg-type]
    assert _saml_request(on).get("ForceAuthn") == "true"
    assert _saml_request(off).get("ForceAuthn") is None
    assert [s["force_reauth"] for s in seen] == [True, False]


def _login(force: bool) -> store.Login:
    return store.Login(
        connection_id="c", config_revision=1, nonce=None, code_verifier=None, request_id=None, client_id=None, return_to=None, link_principal_id=None,
        expires_at=NOW + oidc_login.LOGIN_TTL, force_reauth=force,
    )  # fmt: skip


def test_a_forced_reauthentication_needs_an_authentication_after_the_request() -> None:
    started = NOW
    oidc_login.require_fresh_reauth(_login(True), started + timedelta(seconds=5))
    oidc_login.require_fresh_reauth(
        _login(True), started - timedelta(seconds=30)
    )  # inside the clock skew
    for stale in (started - timedelta(minutes=10), started - timedelta(hours=8), None):
        with pytest.raises(FederationError) as e:
            oidc_login.require_fresh_reauth(_login(True), stale)
        assert e.value.code == "FEDERATION_REAUTH_NOT_FRESH"


def test_an_ordinary_login_is_not_held_to_it() -> None:
    oidc_login.require_fresh_reauth(_login(False), None)
    oidc_login.require_fresh_reauth(_login(False), NOW - timedelta(days=1))
