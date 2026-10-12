"""Architecture guards for the modular monolith (DEC-D6, ZID-SVC-001 §4-6).

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


def _migration_tables(py: Path) -> set[str]:
    """schema.table names a migration creates or alters, including `{table}` loops over a tuple."""
    source = py.read_text(encoding="utf-8")
    loops: dict[str, tuple[str, ...]] = {}
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Tuple):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    loops[target.id] = tuple(
                        e.value for e in node.value.elts if isinstance(e, ast.Constant)
                    )
    found: set[str] = set()
    for schema, table in re.findall(
        r"(?:CREATE|ALTER|DROP)\s+TABLE\s+(?:IF\s+(?:NOT\s+)?EXISTS\s+)?(\w+)\.(\{\w+\}|\w+)",
        source,
        re.IGNORECASE,
    ):
        if table.startswith("{"):
            names = next(iter(loops.values()), ()) if len(loops) == 1 else ()
            assert names, (
                f"{py.name}: can't resolve {table}; use one tuple constant for table loops"
            )
            found |= {f"{schema}.{n}" for n in names}
        else:
            found.add(f"{schema}.{table}")
    return found


def test_migrations_only_touch_tables_their_module_owns() -> None:
    """Each stream may create/alter only the tables table-ownership.yaml gives its module."""
    owned: dict[str, set[str]] = {}
    current = None
    for line in OWNERSHIP.read_text(encoding="utf-8").splitlines():
        if m := re.match(r"^  (\w+\.\w+):$", line):
            current = m.group(1)
        elif (m := re.match(r"^\s+module: (\w+)$", line)) and current:
            owned.setdefault(m.group(1), set()).add(current)
    violations = []
    for py in MIGRATIONS_DIR.glob("*/versions/*.py"):
        stream = py.parent.parent.name
        extra = _migration_tables(py) - owned.get(stream, set())
        violations += [
            f"{stream}/{py.name} touches {t}, owned by another module" for t in sorted(extra)
        ]
    assert not violations, "\n".join(violations)


EVENT_NAME = r"(authority|authority_root|mandate|agent|organization)(.[a-z_]+)+"


def test_emitted_events_are_declared_in_the_catalogue() -> None:
    """ZID-EVENTS-001 §4.1: no undeclared event type reaches the outbox."""
    catalogue = set(
        re.findall(
            r"^  ([a-z0-9_.]+):$",
            (APP.parents[1] / "contracts/asyncapi/event-catalogue.yaml").read_text(
                encoding="utf-8"
            ),
            re.MULTILINE,
        )
    )
    emitted: set[str] = set()
    for py in MODULES_DIR.rglob("*.py"):
        source = py.read_text(encoding="utf-8")
        if "zid_outbox" not in source:
            continue
        for node in ast.walk(ast.parse(source)):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and re.fullmatch(EVENT_NAME, node.value)
            ):
                emitted.add(node.value)
    # root events are built from the status: every status the lifecycle can emit must be declared
    emitted |= {f"authority_root.{s}" for s in ("revoked", "suspended", "stale")}
    assert emitted and not emitted - catalogue, emitted - catalogue
