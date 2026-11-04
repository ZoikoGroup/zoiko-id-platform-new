import pytest
from zid_platform.entrypoints.supervisor import ROLE_SCOPED, plan, uvicorn_command
from zid_platform.roles import Role

BASE = {
    "ZID_CELL_DATABASE_URL": "postgresql+asyncpg://u:p@db/zid_cell",
    "ZID_GLOBAL_DATABASE_URL": "postgresql+asyncpg://u:p@db/zid_global",
    "ZID_CELL_ID": "uk-1",
    "PATH": "/usr/bin",
}


def test_default_runs_both_roles_on_separate_ports_and_databases() -> None:
    cell, glob = plan(BASE)
    assert (cell.role, cell.port, glob.role, glob.port) == (Role.CELL, 8000, Role.GLOBAL, 8100)
    assert cell.env["ZID_DATABASE_URL"].endswith("/zid_cell") and cell.env["ZID_CELL_ID"] == "uk-1"
    assert (
        glob.env["ZID_DATABASE_URL"].endswith("/zid_global") and glob.env["ZID_CELL_ID"] == "global"
    )


def test_roles_never_see_each_others_database_url() -> None:
    cell, glob = plan({**BASE, "ZID_CELL_PORT": "8000"})
    assert not (ROLE_SCOPED & {*cell.env, *glob.env})  # per-role DB URLs/ports never reach a role
    assert (
        "zid_global" not in cell.env["ZID_DATABASE_URL"]
        and "zid_cell" not in glob.env["ZID_DATABASE_URL"]
    )


def test_missing_role_database_is_not_inherited() -> None:
    (glob,) = plan(
        {"ZID_ROLES": "global", "ZID_DATABASE_URL": "postgresql+asyncpg://u:p@db/zid_cell"}
    )
    assert "ZID_DATABASE_URL" not in glob.env  # global must never fall back to a cell database


def test_production_cell_runs_single_role() -> None:
    (only,) = plan({**BASE, "ZID_ROLES": "cell"})
    assert only.role is Role.CELL


@pytest.mark.parametrize("roles", ["", "cell,cell", "tenant"])
def test_invalid_role_lists_are_rejected(roles: str) -> None:
    with pytest.raises(ValueError):
        plan({**BASE, "ZID_ROLES": roles})


def test_port_clash_is_rejected() -> None:
    with pytest.raises(ValueError):
        plan({**BASE, "ZID_GLOBAL_PORT": "8000"})


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
