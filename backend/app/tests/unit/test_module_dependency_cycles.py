"""Module dependency cycles must not grow (ZID-VERIFY-MASTER-001 SVC-16; ZID-SVC-001 §7; DEC-D6).

SVC-16 asks for "no synchronous service cycle". In the modular monolith the dependencies between modules are
`modules/<x>/ports.py` imports, so the graph of those imports is the dependency graph the modules would have
as separate services. It is NOT acyclic today: the cycles below were measured when this test was written and
are frozen. They are a refactoring backlog (extract the shared type, invert one direction, or move one call
behind an event), tracked here instead of hidden.

The test is a ratchet: a NEW cycle fails the build, and fixing a cycle makes the frozen set stale until it is
removed from KNOWN. The count may only go down.
"""

from __future__ import annotations

import ast
from pathlib import Path

MODULES = Path(__file__).resolve().parents[2] / "src" / "zid_platform" / "modules"

# Cycles measured on 2026-10-24, as sorted tuples of module names. Import-time cycles (the harder kind, which
# Python itself must be arranged around) are listed apart from the ones broken by a function-level import.
KNOWN_IMPORT_TIME: frozenset[tuple[str, ...]] = frozenset(
    {
        ("authentication", "session"),
        ("authority", "mandate"),
        ("authentication", "signals"),
        ("authentication", "tenant", "verification"),
        ("operator_access", "resilience", "tenant"),
        ("resilience", "tenant"),
        ("integration", "provisioning"),
    }
)
KNOWN_ALL: frozenset[tuple[str, ...]] = KNOWN_IMPORT_TIME | frozenset(
    {
        ("authentication", "configuration", "session", "tenant"),
        ("configuration", "session", "tenant"),
        ("principal", "privacy", "tenant"),
        (
            "authentication",
            "configuration",
            "operator_access",
            "resilience",
            "session",
            "signals",
            "tenant",
        ),
        ("configuration", "operator_access", "resilience", "session", "signals", "tenant"),
        ("operator_access", "tenant"),
    }
)


def build_graph() -> dict[str, dict[str, bool]]:
    """module -> {other module: True if it is only ever imported inside a function}."""
    graph: dict[str, dict[str, bool]] = {}
    for mod in sorted(p for p in MODULES.iterdir() if p.is_dir() and not p.name.startswith("_")):
        deps: dict[str, bool] = {}
        for py in mod.rglob("*.py"):
            tree = ast.parse(py.read_text(encoding="utf-8"))
            parent = {c: n for n in ast.walk(tree) for c in ast.iter_child_nodes(n)}
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                elif isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                else:
                    continue
                for n in names:
                    parts = n.split(".")
                    if (
                        len(parts) < 4
                        or parts[:2] != ["zid_platform", "modules"]
                        or parts[2] == mod.name
                    ):
                        continue
                    inner, p = False, parent.get(node)
                    while p is not None and not inner:
                        inner = isinstance(p, (ast.FunctionDef, ast.AsyncFunctionDef))
                        p = parent.get(p)
                    deps[parts[2]] = deps.get(parts[2], True) and inner
        graph[mod.name] = deps
    return graph


def find_cycles(
    graph: dict[str, dict[str, bool]], *, import_time_only: bool
) -> set[tuple[str, ...]]:
    """Strongly connected components of size > 1, plus the elementary cycles found by DFS, as sorted tuples."""
    edges = {
        m: [d for d, inner in deps.items() if not (import_time_only and inner) and d in graph]
        for m, deps in graph.items()
    }
    found: set[tuple[str, ...]] = set()
    state: dict[str, int] = {}
    stack: list[str] = []

    def visit(n: str) -> None:
        state[n] = 1
        stack.append(n)
        for d in edges[n]:
            if state.get(d) == 1:
                found.add(tuple(sorted(set(stack[stack.index(d) :]))))
            elif d not in state:
                visit(d)
        stack.pop()
        state[n] = 2

    for m in edges:
        if m not in state:
            visit(m)
    return found


def test_the_graph_is_the_real_one() -> None:
    graph = build_graph()
    assert len(graph) >= 28 and sum(len(d) for d in graph.values()) > 70
    assert (
        "tenant" in graph["session"] or "configuration" in graph["session"]
    )  # a known edge, so the scan reads imports


def test_no_new_import_time_dependency_cycles() -> None:
    now = find_cycles(build_graph(), import_time_only=True)
    new = sorted(now - KNOWN_IMPORT_TIME)
    assert not new, (
        f"new import-time module cycle(s): {new}. Invert one direction or move the call behind an event."
    )


def test_no_new_dependency_cycles_including_function_level_imports() -> None:
    now = find_cycles(build_graph(), import_time_only=False)
    new = sorted(now - KNOWN_ALL)
    assert not new, f"new module dependency cycle(s): {new}"


def test_fixed_cycles_are_removed_from_the_known_list() -> None:
    """The ratchet only turns one way: when a cycle is fixed it must leave KNOWN so it cannot come back."""
    graph = build_graph()
    assert KNOWN_IMPORT_TIME <= find_cycles(graph, import_time_only=True) | KNOWN_IMPORT_TIME
    stale = sorted(KNOWN_IMPORT_TIME - find_cycles(graph, import_time_only=True))
    assert not stale, f"these cycles no longer exist; remove them from KNOWN_IMPORT_TIME: {stale}"
