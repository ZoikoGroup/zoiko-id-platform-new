"""Architecture guards for the modular monolith (ADR-0031, ZID-SVC-001 §4-6).

These tests fail the build if the monolith starts turning into a shared-model monolith.
"""

import ast
import re
from pathlib import Path

from zid_platform.roles import PLANES, Role, modules_for, schemas_for

APP = Path(__file__).resolve().parents[2]
MODULES_DIR = APP / "src" / "zid_platform" / "modules"
MIGRATIONS_DIR = APP / "migrations"
OWNERSHIP = APP.parent / "database" / "table-ownership.yaml"
# Event Platform tables (events.outbox_event / inbox_message)
PLATFORM_MIGRATION_STREAMS = {"outbox"}

ON_DISK = {p.name for p in MODULES_DIR.iterdir() if p.is_dir() and not p.name.startswith("__")}
IN_PLANES = [m for mods in PLANES.values() for m in mods]


def test_every_module_belongs_to_exactly_one_plane() -> None:
    assert sorted(IN_PLANES) == sorted(set(IN_PLANES)), "module listed in two planes"
    assert set(IN_PLANES) == ON_DISK, f"plane map and modules/ differ: {set(IN_PLANES) ^ ON_DISK}"


def test_global_role_never_loads_cell_modules() -> None:
    assert set(modules_for(Role.GLOBAL)) == {"locator", "discovery"}
    assert not set(modules_for(Role.CELL)) & set(modules_for(Role.GLOBAL))
    assert "global_directory" not in schemas_for(Role.CELL)


def test_every_migration_stream_belongs_to_a_module() -> None:
    streams = {p.name for p in MIGRATIONS_DIR.iterdir() if p.is_dir()}
    assert streams - PLATFORM_MIGRATION_STREAMS <= ON_DISK


def test_table_owners_are_real_modules() -> None:
    owners = set(
        re.findall(r"^\s+module: (\w+)$", OWNERSHIP.read_text(encoding="utf-8"), re.MULTILINE)
    )
    assert owners - PLATFORM_MIGRATION_STREAMS <= ON_DISK, owners - ON_DISK


def test_modules_only_import_other_modules_through_ports() -> None:
    violations = []
    for py in MODULES_DIR.rglob("*.py"):
        own = py.relative_to(MODULES_DIR).parts[0]
        for node in ast.walk(ast.parse(py.read_text(encoding="utf-8"))):
            names = (
                [node.module or ""]
                if isinstance(node, ast.ImportFrom)
                else ([a.name for a in node.names] if isinstance(node, ast.Import) else [])
            )
            for name in names:
                parts = name.split(".")
                other_module = (
                    parts[:2] == ["zid_platform", "modules"] and len(parts) > 2 and parts[2] != own
                )
                if other_module and parts[3:4] != ["ports"]:
                    violations.append(f"{py.relative_to(APP)} imports {name}")
    assert not violations, "cross-module imports must go through ports.py:\n" + "\n".join(
        violations
    )
