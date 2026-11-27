import pytest
from zid_platform.entrypoints.supervisor import ROLE_SCOPED, plan, uvicorn_command
from zid_platform.roles import Role

BASE = {
    "ZID_CELL_DATABASE_URL": "postgresql+asyncpg://u:p@db/zid_cell",
    "ZID_CELL_ID": "uk-1",
    "PATH": "/usr/bin",
}


def test_default_is_one_process_on_one_port() -> None:
    (cell,) = plan(BASE)
    assert (cell.role, cell.port) == (Role.CELL, 8000)
    assert cell.env["ZID_DATABASE_URL"].endswith("/zid_cell") and cell.env["ZID_CELL_ID"] == "uk-1"


def test_serving_the_global_plane_from_the_cell_needs_only_the_flag() -> None:
    (cell,) = plan({**BASE, "ZID_SERVE_GLOBAL_PLANE": "true"})
    assert (
        cell.env["ZID_SERVE_GLOBAL_PLANE"] == "true" and cell.port == 8000
    )  # still one process, one port


def test_the_cell_always_serves_the_global_plane_from_the_one_process() -> None:
    (cell,) = plan(BASE)  # even when the flag is not set, the plane is on the same port
    assert cell.env["ZID_SERVE_GLOBAL_PLANE"] == "true" and cell.port == 8000


@pytest.mark.parametrize("roles", ["cell,global", "global", "", "cell,cell", "tenant"])
def test_only_the_cell_role_runs(roles: str) -> None:
    with pytest.raises(ValueError):
        plan({**BASE, "ZID_ROLES": roles})


@pytest.mark.parametrize(
    "extra",
    [
        {"ZID_GLOBAL_PORT": "8100"},
        {"ZID_GLOBAL_DATABASE_URL": "postgresql+asyncpg://u:p@db/elsewhere"},
        {"ZID_CELL_PORT": "9000"},
    ],
)
def test_a_second_port_or_database_is_refused(extra: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        plan({**BASE, **extra})


def test_port_8000_may_be_stated() -> None:
    (cell,) = plan({**BASE, "ZID_CELL_PORT": "8000"})
    assert cell.port == 8000


def test_per_role_settings_never_reach_the_process() -> None:
    (cell,) = plan({**BASE, "ZID_CELL_PORT": "8000"})
    assert not (
        ROLE_SCOPED & set(cell.env)
    )  # the supervisor's own settings are consumed, not passed on


def test_a_bare_database_url_is_not_inherited() -> None:
    (cell,) = plan({"ZID_DATABASE_URL": "postgresql+asyncpg://u:p@db/other"})
    assert "ZID_DATABASE_URL" not in cell.env


def test_the_ingress_is_trusted_only_when_named_so_sign_up_limits_see_the_real_client() -> None:
    """Behind an ingress every caller shares its address unless the proxy is trusted: named addresses are passed to uvicorn, nothing is
    passed when none is set, and believing any sender's X-Forwarded-For is allowed only locally."""
    (only,) = plan({**BASE, "ZID_ROLES": "cell"})
    plain = uvicorn_command(only, BASE)
    assert "--forwarded-allow-ips" not in plain and "--no-access-log" in plain
    named = uvicorn_command(only, {**BASE, "ZID_FORWARDED_ALLOW_IPS": "10.0.0.7,10.0.0.8"})
    assert (
        named[named.index("--forwarded-allow-ips") + 1] == "10.0.0.7,10.0.0.8"
        and "--proxy-headers" in named
    )
    assert "--forwarded-allow-ips" in uvicorn_command(
        only, {**BASE, "ZID_FORWARDED_ALLOW_IPS": "*", "ZID_ENVIRONMENT": "local"}
    )
    with pytest.raises(ValueError):
        uvicorn_command(only, {**BASE, "ZID_FORWARDED_ALLOW_IPS": "*", "ZID_ENVIRONMENT": "prod"})
    with pytest.raises(ValueError):  # an environment that is not named is not local
        uvicorn_command(
            only, {**BASE, "ZID_FORWARDED_ALLOW_IPS": "*", "ZID_ENVIRONMENT": "preprod"}
        )
