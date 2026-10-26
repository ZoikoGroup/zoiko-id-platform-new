"""Detection coverage matrix (ZID-SEC-001 SEC-26): every mapped technique has a control and a test that proves it fires."""

from __future__ import annotations

import ast
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[4]
MATRIX = REPO / "docs/threat-model/detection-coverage.yaml"
REQUIRED_FAMILIES = {"credential", "privilege", "supply-chain", "cloud"}


def _entries() -> list[dict[str, str]]:
    return yaml.safe_load(MATRIX.read_text(encoding="utf-8"))["techniques"]  # type: ignore[no-any-return]


def _defines(path: Path, name: str) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return any(
        isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef) and n.name == name
        for n in ast.walk(tree)
    )


def test_every_mapped_technique_names_a_control_and_a_test_that_exists() -> None:
    """SEC-26: a technique is only listed with a proof that runs a representative attack against its control."""
    entries = _entries()
    assert len(entries) >= 6
    for e in entries:
        assert {"technique", "family", "control", "detects", "proof"} <= set(e), e
        assert e["technique"].startswith("T") and e["technique"][1:5].isdigit(), e
        path, _, test = e["proof"].partition("::")
        assert (REPO / path).is_file() and _defines(REPO / path, test), (
            f"missing proof {e['proof']}"
        )
    assert len({e["technique"] for e in entries}) == len(entries)


def test_the_credential_privilege_supply_chain_and_cloud_families_are_all_covered() -> None:
    """SEC-26: the representative set spans the four families the acceptance row names."""
    assert REQUIRED_FAMILIES <= {e["family"] for e in _entries()}
