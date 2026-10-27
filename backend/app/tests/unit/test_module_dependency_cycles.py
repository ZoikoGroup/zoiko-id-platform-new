"""Module dependency cycles must not grow (ZID-VERIFY-MASTER-001 SVC-16; ZID-SVC-001 §7; DEC-D6).

SVC-16 asks for "no synchronous service cycle". In the modular monolith the dependencies between modules are
`modules/<x>/ports.py` imports, so the graph of those imports is the dependency graph the modules would have
as separate services. It is NOT acyclic today: the cycles below were measured when this test was written and
are frozen. They are a refactoring backlog (extract the shared type, invert one direction, or move one call
behind an event), tracked here instead of hidden.

The test is a ratchet: a NEW cycle fails the build, and fixing a cycle makes the frozen set stale until it is
removed from KNOWN. The count may only go down.

Two measures, both independent of the order a file system lists files in (Windows and the Linux CI runner differ):
the import-time cycles, found by a depth-first walk over sorted edges, and the groups of modules that sit in a cycle
at all (strongly connected components, which are the same whichever way they are walked). The function-level check
used to compare the cycles a depth-first walk happened to report, and that set changed with the visiting order, so a
cycle list measured on one machine failed on another with no code change.
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
# The groups of modules that are mutually dependent once function-level imports count too, measured on commit
# a5dcb58 (before the payment and mandate work, which added no module to any group). A module may not join a group, and
# two groups may not merge. When a group shrinks, shrink it here.
KNOWN_GROUPS: frozenset[tuple[str, ...]] = frozenset(
    {
        (
            "authentication",
            "authority",
            "configuration",
            "mandate",
            "operator_access",
            "principal",
            "privacy",
            "resilience",
            "session",
            "signals",
            "tenant",
            "verification",
        ),
        ("integration", "provisioning"),
    }
)


def build_graph() -> dict[str, dict[str, bool]]:
    """module -> {other module: True if it is only ever imported inside a function}."""
    graph: dict[str, dict[str, bool]] = {}
    for mod in sorted(p for p in MODULES.iterdir() if p.is_dir() and not p.name.startswith("_")):
        deps: dict[str, bool] = {}
        for py in sorted(
            mod.rglob("*.py")
        ):  # sorted: the order must not depend on the operating system
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
        m: sorted(d for d, inner in deps.items() if not (import_time_only and inner) and d in graph)
        for m, deps in sorted(graph.items())
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

    for m in sorted(edges):
        if m not in state:
            visit(m)
    return found


def cyclic_groups(graph: dict[str, dict[str, bool]]) -> set[tuple[str, ...]]:
    """Groups of modules that can all reach each other (strongly connected components of more than one module)."""
    edges = {m: sorted(d for d in deps if d in graph) for m, deps in sorted(graph.items())}
    order: list[str] = []
    seen: set[str] = set()

    def first(n: str) -> None:
        seen.add(n)
        for d in edges[n]:
            if d not in seen:
                first(d)
        order.append(n)

    for m in sorted(edges):
        if m not in seen:
            first(m)
    back: dict[str, list[str]] = {m: [] for m in edges}
    for m, ds in edges.items():
        for d in ds:
            back[d].append(m)
    placed: set[str] = set()
    groups: set[tuple[str, ...]] = set()

    def second(n: str, into: list[str]) -> None:
        placed.add(n)
        into.append(n)
        for d in sorted(back[n]):
            if d not in placed:
                second(d, into)

    for n in reversed(order):
        if n not in placed:
            found: list[str] = []
            second(n, found)
            if len(found) > 1:
                groups.add(tuple(sorted(found)))
    return groups


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


def test_no_module_joins_a_dependency_cycle_including_function_level_imports() -> None:
    now = cyclic_groups(build_graph())
    grown = sorted(g for g in now if not any(set(g) <= set(k) for k in KNOWN_GROUPS))
    assert not grown, (
        f"a module joined a dependency cycle, or two groups merged: {grown}. "
        "Invert one direction or move the call behind an event."
    )


def test_fixed_cycles_are_removed_from_the_known_list() -> None:
    """The ratchet only turns one way: when a cycle is fixed it must leave KNOWN so it cannot come back."""
    graph = build_graph()
    assert KNOWN_IMPORT_TIME <= find_cycles(graph, import_time_only=True) | KNOWN_IMPORT_TIME
    stale = sorted(KNOWN_IMPORT_TIME - find_cycles(graph, import_time_only=True))
    assert not stale, f"these cycles no longer exist; remove them from KNOWN_IMPORT_TIME: {stale}"


def test_groups_that_shrink_are_removed_from_the_known_list() -> None:
    now = {m for g in cyclic_groups(build_graph()) for m in g}
    gone = sorted({m for g in KNOWN_GROUPS for m in g} - now)
    assert not gone, (
        f"these modules are no longer in any cycle; remove them from KNOWN_GROUPS: {gone}"
    )
