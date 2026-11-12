"""The counsel worksheet lists every COUNSEL field, each with a question, and cannot go stale (ZID-PRI-004 section 16, 26)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
spec = importlib.util.spec_from_file_location(
    "counsel_worksheet", ROOT / "tools" / "scripts" / "counsel_worksheet.py"
)
cw = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
sys.modules["counsel_worksheet"] = cw
spec.loader.exec_module(cw)  # type: ignore[union-attr]


def test_every_counsel_field_has_a_question_so_a_blank_cannot_be_added_silently() -> None:
    rows = cw.fields()
    assert rows, "the configuration ships with counsel's fields open"
    assert cw.unmapped(rows) == [], "a COUNSEL field needs a question in QUESTIONS"


def test_the_written_worksheet_is_current() -> None:
    assert cw.DEFAULT_OUT.read_text(encoding="utf-8") == cw.render(cw.fields()), (
        "run tools/scripts/counsel_worksheet.py --write"
    )


def test_it_finds_the_privacy_files_and_every_breach_jurisdiction() -> None:
    rows = cw.fields()
    files = {r["file"] for r in rows}
    assert {
        "privacy/adm-rules.yaml",
        "privacy/collection-rules.yaml",
        "privacy/breach-clocks.yaml",
    } <= files
    clocks = [r for r in rows if r["file"].endswith("breach-clocks.yaml")]
    assert len(clocks) >= 10 and {r["key"] for r in clocks} == {"hours_to_notify_authority"}


def test_a_field_leaves_the_worksheet_only_when_counsels_value_replaces_the_word(
    tmp_path: Path,
) -> None:
    (tmp_path / "x.yaml").write_text(
        "packs:\n  p:\n    approval_ref: COUNSEL\n    effective_from: 2026-01-01\n",
        encoding="utf-8",
    )
    rows = cw.fields(tmp_path)
    assert [r["path"] for r in rows] == ["packs.p.approval_ref"], (
        "a filled field is gone, an open one stays"
    )
    (tmp_path / "x.yaml").write_text(
        "packs:\n  p:\n    approval_ref: REF-1\n    effective_from: 2026-01-01\n", encoding="utf-8"
    )
    assert cw.fields(tmp_path) == []


def test_an_unknown_counsel_field_is_reported_not_guessed(tmp_path: Path) -> None:
    (tmp_path / "x.yaml").write_text("a:\n  brand_new_rule: COUNSEL\n", encoding="utf-8")
    assert cw.unmapped(cw.fields(tmp_path)) == ["x.yaml: a.brand_new_rule"]
