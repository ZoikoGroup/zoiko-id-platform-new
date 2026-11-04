"""The go-live board and the evidence intake decide nothing (ZID-EDR-001 section 4; ZID-COM-RAT-011)."""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
from types import ModuleType

import pytest

REPO = pathlib.Path(__file__).resolve().parents[4]


def load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, REPO / "tools" / "scripts" / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_the_board_lists_every_wave_one_country_with_six_open_approvals_and_none_live() -> None:
    board = load("go_live_board").build()
    wave_one = {c["country"]: c for c in board["countries"] if c["wave"] == 1}
    assert set(wave_one) == {"US", "GB", "IE", "DE", "FR"}
    for c in wave_one.values():
        assert len(c["approvals"]) == 6
        assert all(a["state"] == "OPEN" for a in c["approvals"].values()), c[
            "country"
        ]  # nobody recorded an approval: the board never invents one
        assert c["live"] is False
    assert board["release"]["disposition"] == "NO-GO"


def test_attached_evidence_shows_as_attached_and_as_changed_when_the_file_is_edited(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    intake, board = load("record_evidence"), load("go_live_board")
    (tmp_path / "deploy" / "release").mkdir(parents=True)
    manifest = tmp_path / "deploy" / "release" / "evidence-manifest.json"
    for module in (intake, board):
        monkeypatch.setattr(module, "ROOT", tmp_path)
        monkeypatch.setattr(module, "MANIFEST", manifest)
    pdf = tmp_path / "legal-gb.pdf"
    pdf.write_bytes(b"%PDF signed assessment")
    entry = intake.record(
        "LEGAL-GB-1", pdf, "Counsel, Legal", ["legal_assessment:GB"], "UK assessment"
    )
    assert (
        entry["sha256"]
        and json.loads(manifest.read_text(encoding="utf-8"))["evidence"][0]["ref"] == "LEGAL-GB-1"
    )
    fresh = board._freshness("LEGAL-GB-1", board._manifest())
    assert fresh == "ATTACHED"
    pdf.write_bytes(b"%PDF edited afterwards")
    assert board._freshness("LEGAL-GB-1", board._manifest()) == "CHANGED"
    pdf.unlink()
    assert board._freshness("LEGAL-GB-1", board._manifest()) == "MISSING"
    assert (
        board._freshness("SOMETHING-ELSE", board._manifest()) == "RECORDED"
    )  # a reference the owners wrote that names no attached file
    assert board._freshness(None, board._manifest()) == "OPEN"


def test_a_reference_is_never_reused_and_a_file_outside_the_repository_is_refused(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    intake = load("record_evidence")
    (tmp_path / "deploy" / "release").mkdir(parents=True)
    monkeypatch.setattr(intake, "ROOT", tmp_path)
    monkeypatch.setattr(
        intake, "MANIFEST", tmp_path / "deploy" / "release" / "evidence-manifest.json"
    )
    inside = tmp_path / "a.txt"
    inside.write_text("x", encoding="utf-8")
    intake.record("R-1", inside, "Founder", ["G0"])
    with pytest.raises(SystemExit):
        intake.record("R-1", inside, "Founder", ["G0"])
    with pytest.raises(SystemExit):
        intake.record("R-2", pathlib.Path(__file__), "Founder", ["G0"])
