"""An operator who signed in with email and password is never asked for a passkey proof; no one else is excused."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from zid_platform.modules.authentication.api import step_up
from zid_platform.modules.session.ports import SessionStatus

OP_TENANT = "01a1a56f-0000-7000-8000-000000000001"


def _request(tenant_id: str | None = OP_TENANT):
    state = SimpleNamespace(operator_settings=SimpleNamespace(tenant_id=tenant_id))
    return SimpleNamespace(app=SimpleNamespace(state=state))


def _caller(tenant: str = OP_TENANT, session_id: str | None = "s1"):
    return SimpleNamespace(session_id=session_id, ctx=SimpleNamespace(tenant_id=tenant))


def _check(
    monkeypatch,
    *,
    context="operator:password",
    status=SessionStatus.ACTIVE,
    request=None,
    caller=None,
):
    async def fake_load(_conn, _session_id):
        return SimpleNamespace(authentication_context=context, status=status)

    monkeypatch.setattr(step_up, "load_session", fake_load)
    return asyncio.run(
        step_up._operator_password_session(request or _request(), None, caller or _caller())
    )


def test_operator_password_session_is_excused(monkeypatch):
    assert _check(monkeypatch) is True


def test_a_passkey_session_is_not_excused(monkeypatch):
    assert _check(monkeypatch, context="native:webauthn") is False


def test_a_revoked_session_is_not_excused(monkeypatch):
    assert _check(monkeypatch, status=SessionStatus.REVOKED) is False


def test_a_customer_tenant_is_not_excused(monkeypatch):
    assert (
        _check(monkeypatch, caller=_caller(tenant="01a1a56f-0000-7000-8000-0000000000ff")) is False
    )


def test_no_operator_tenant_configured_excuses_nobody(monkeypatch):
    assert _check(monkeypatch, request=_request(tenant_id=None)) is False


def test_no_session_is_not_excused(monkeypatch):
    assert _check(monkeypatch, caller=_caller(session_id=None)) is False
