"""The mesh proxy proves itself, and the cell refuses to start deployed without that proof (CR-064/CR-065).

A forwarded client certificate is believed only from a trusted proxy. An address alone is weak on a shared host, so a deployed cell also needs the
secret the proxy adds. These pin the settings that carry it and the start-up rules around it, and the one other start-up rule from the builds change."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from zid_crypto import KeyRing, LocalSigner
from zid_platform.main import create_app
from zid_platform.settings import Settings
from zid_platform.workload_identity import trust_policy_from_settings


def settings(**kw: Any) -> Settings:
    return Settings(
        token_issuer="https://i.example",
        token_audience="https://a.example",
        public_base_url="https://a.example",
        **kw,
    )


def start(**kw: Any) -> None:
    create_app(settings(**kw), keyring=KeyRing(LocalSigner("k")))


def test_no_secret_file_means_no_secret_and_the_address_rule_alone() -> None:
    assert (
        trust_policy_from_settings(settings(mesh_trusted_proxies=("127.0.0.1",))).proxy_secret
        is None
    )


def test_the_secret_is_read_from_a_file_and_trimmed(tmp_path: Path) -> None:
    f = tmp_path / "secret"
    f.write_text("a" * 40 + "\n", encoding="utf-8")
    policy = trust_policy_from_settings(
        settings(mesh_trusted_proxies=("127.0.0.1",), mesh_proxy_secret_file=str(f))
    )
    assert policy.proxy_secret == b"a" * 40


def test_a_short_secret_is_refused_rather_than_used(tmp_path: Path) -> None:
    f = tmp_path / "secret"
    f.write_text("short", encoding="utf-8")
    with pytest.raises(RuntimeError, match="at least 32 characters"):
        trust_policy_from_settings(
            settings(mesh_trusted_proxies=("127.0.0.1",), mesh_proxy_secret_file=str(f))
        )


@pytest.mark.parametrize("env", ["staging", "production", "prod"])
def test_a_deployed_cell_with_trusted_proxies_and_no_secret_does_not_start(env: str) -> None:
    with pytest.raises(RuntimeError, match="ZID_MESH_PROXY_SECRET_FILE"):
        start(environment=env, mesh_trusted_proxies=("172.30.0.250",))


@pytest.mark.parametrize("env", ["local", "test"])
def test_local_and_test_may_trust_a_proxy_by_address(env: str) -> None:
    start(environment=env, mesh_trusted_proxies=("127.0.0.1",))


def test_the_secret_satisfies_the_deployed_rule(tmp_path: Path) -> None:
    f = tmp_path / "secret"
    f.write_text("k" * 40, encoding="utf-8")
    # a deployed cell needs more than this to start (a key provider, for one), so look only at whether THIS rule fired
    with pytest.raises(Exception) as e:
        start(
            environment="production",
            mesh_trusted_proxies=("172.30.0.250",),
            mesh_proxy_secret_file=str(f),
        )
    assert "ZID_MESH_PROXY_SECRET_FILE" not in str(e.value)


@pytest.mark.parametrize("env", ["staging", "production"])
def test_a_deployed_cell_cannot_switch_the_approved_build_requirement_off(env: str) -> None:
    with pytest.raises(RuntimeError, match="ZID_AGENT_BUILDS=off"):
        start(environment=env, agent_builds="off")


def test_off_is_allowed_where_the_requirement_is_not_default() -> None:
    start(environment="local", agent_builds="off")
