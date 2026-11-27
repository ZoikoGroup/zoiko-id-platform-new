"""The browser bundle secret scan (ZID-CP-WF10 v2.0 row 4-05; CR-173). Every secret below is built from pieces here so this file is not itself a finding."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[4]
spec = importlib.util.spec_from_file_location(
    "bundle_secret_scan", REPO / "tools" / "scripts" / "bundle_secret_scan.py"
)
assert spec and spec.loader
scan = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scan)  # type: ignore[union-attr]

TAIL = "A1b2C3d4E5f6G7h8I9j0"
SHAPES = {
    "verify-api-client-secret": "const s='" + "zps" + "_" + TAIL + "';",
    "verify-api-access-token": "headers={Authorization:'Bearer " + "zat" + "_" + TAIL + "'}",
    "verify-api-launch-link": "location='/verify/" + "zvl" + "_" + TAIL + "'",
    "private-key-block": "k='-----BEGIN " + "PRIVATE KEY-----'",
    "payment-secret-key": "stripe('" + "sk" + "_live_" + TAIL + "')",
    "webhook-signing-secret": "w='" + "whsec" + "_" + TAIL + "'",
    "cloud-access-key": "id='" + "AKIA" + "ABCDEFGHIJKLMNOP'",
    "literal-client-secret": "cfg={client_secret:'" + TAIL + "'}",
}


@pytest.mark.parametrize("rule", sorted(SHAPES))
def test_each_kind_of_secret_is_found_by_name(rule: str, tmp_path: Path) -> None:
    (tmp_path / "chunk.js").write_text("var a=1;\n" + SHAPES[rule] + "\n", encoding="utf-8")
    files, findings = scan.scan_dir(tmp_path)
    assert files == 1 and findings == [f"chunk.js:2: {rule}"]


def test_a_finding_never_contains_the_secret_so_a_log_cannot_leak_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "a.js").write_text(SHAPES["verify-api-client-secret"], encoding="utf-8")
    assert scan.main([str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert "verify-api-client-secret" in out and TAIL not in out


def test_placeholders_and_ordinary_words_are_not_findings(tmp_path: Path) -> None:
    (tmp_path / "ok.js").write_text(
        "// zps_ is the prefix; send zat_xxx as the bearer\nconst prefix='zps_';const note='client_secret is never sent from a browser';\n",
        encoding="utf-8",
    )
    assert scan.scan_dir(tmp_path)[1] == []
    assert scan.main([str(tmp_path)]) == 0


def test_an_empty_folder_or_a_missing_one_fails_because_nothing_scanned_proves_nothing(
    tmp_path: Path,
) -> None:
    assert scan.main([str(tmp_path)]) == 2
    assert scan.main([str(tmp_path / "missing")]) == 2
    assert scan.main([]) == 2


def test_the_real_console_bundle_has_no_secret_when_it_has_been_built() -> None:
    static = REPO / "frontend" / "apps" / "web" / ".next" / "static"
    if not static.is_dir():
        pytest.skip("the console has not been built here; CI scans the real build")
    files, findings = scan.scan_dir(static)
    assert files > 0 and findings == []
