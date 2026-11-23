"""CR-144: API-based agents ship OFF in production until a named decision switches them on, and fail closed when nothing says otherwise."""

from __future__ import annotations

import pytest
from zid_errors import ZidError
from zid_platform import api_agents_switch as sw
from zid_platform.modules.machine.api.api_agents import _require_api_agents
from zid_platform.modules.session.application.authenticate import _is_api_agent_credential


@pytest.fixture(autouse=True)
def _clean() -> None:
    sw.reset()
    yield
    sw.reset()


@pytest.mark.parametrize(
    ("setting", "environment", "expected"),
    [
        ("auto", "production", False),  # the default in production is OFF
        ("auto", "local", True),
        ("auto", "test", True),
        ("auto", "dev", True),
        ("auto", "", True),
        ("on", "production", True),  # a named decision turns it on
        ("off", "local", False),  # and it can be turned off anywhere
        ("off", "production", False),
    ],
)
def test_the_switch_resolves_from_the_setting_and_the_environment(
    setting: str, environment: str, expected: bool
) -> None:
    assert sw.resolve(setting, environment) is expected
    assert sw.configure(setting, environment) is expected and sw.enabled() is expected


def test_a_process_that_was_never_told_reads_the_environment_and_is_off_in_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ZID_ENVIRONMENT", "production")
    monkeypatch.delenv("ZID_API_AGENTS", raising=False)
    assert sw.enabled() is False  # unconfigured and in production: off, never on by omission
    monkeypatch.setenv("ZID_API_AGENTS", "on")
    assert sw.enabled() is True
    monkeypatch.setenv("ZID_ENVIRONMENT", "test")
    monkeypatch.setenv("ZID_API_AGENTS", "off")
    assert sw.enabled() is False
    monkeypatch.delenv("ZID_ENVIRONMENT")
    monkeypatch.delenv("ZID_API_AGENTS")
    assert sw.enabled() is True  # a script or a test with no environment named is not production


def test_off_means_the_routes_are_a_404_and_a_token_already_issued_gets_no_elevated_assurance() -> (
    None
):
    sw.configure("off", "production")
    with pytest.raises(ZidError) as e:
        _require_api_agents()
    assert e.value.http_status == 404 and e.value.code == "API_AGENTS_DISABLED"
    assert _is_api_agent_credential("api-agent:client-1", frozenset({"workload"}), True) is False

    sw.configure("on", "production")
    _require_api_agents()  # on: no error
    assert _is_api_agent_credential("api-agent:client-1", frozenset({"workload"}), True) is True
    assert (
        _is_api_agent_credential("api-agent:client-1", frozenset({"workload"}), False) is False
    )  # the proof of the key is still needed
