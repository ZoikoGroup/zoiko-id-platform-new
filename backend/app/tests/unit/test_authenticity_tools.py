"""The authenticity gate's policy file and lab command (T24, T25; CR-125)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
import yaml
from zid_platform.modules.verification.domain import authenticity_evaluation as ae
from zid_platform.modules.verification.infrastructure import authenticity_policy as ap

REPO = Path(__file__).resolve().parents[4]
POLICY = {
    "policy": {
        "min_genuine": 2,
        "min_attacks": 2,
        "max_attack_accept_rate": 0,
        "max_genuine_reject_rate": 0.5,
        "max_genuine_inconclusive_rate": 0.5,
        "required_attack_kinds": ["print_copy", "text_edit"],
        "min_devices": 1,
    }
}


def row(**kw):
    base = {
        "document_type": "GB-PASSPORT",
        "check": "TEMPLATE",
        "genuine": True,
        "source": "SPECIMEN",
        "device_model": "Pixel 8",
        "verdict": "PASS",
    }
    base.update(kw)
    return base


GOOD = [
    row(),
    row(),
    row(genuine=False, attack_kind="PRINT_COPY", verdict="FAIL"),
    row(genuine=False, attack_kind="TEXT_EDIT", verdict="FAIL"),
]


def tool():
    spec = importlib.util.spec_from_file_location(
        "authenticity_eval_cli", REPO / "tools" / "scripts" / "authenticity_eval.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_shipped_policy_records_none_so_nothing_can_pass() -> None:
    assert ap.load() is None


def test_a_policy_is_read_strictly() -> None:
    p = ap.parse(POLICY)
    assert (
        p is not None
        and p.required_attack_kinds == frozenset({"PRINT_COPY", "TEXT_EDIT"})
        and p.max_attack_accept_rate == 0.0
    )
    for bad in (
        {"policy": {**POLICY["policy"], "min_genuine": 0}},
        {"policy": {**POLICY["policy"], "max_attack_accept_rate": 1.5}},
        {"policy": {**POLICY["policy"], "required_attack_kinds": ["WISHFUL"]}},
        {"policy": {**POLICY["policy"], "required_attack_kinds": []}},
        {"policy": {**POLICY["policy"], "surprise": 1}},
        {"policy": {k: v for k, v in POLICY["policy"].items() if k != "min_devices"}},
        {"policy": {**POLICY["policy"], "min_devices": True}},
    ):
        with pytest.raises(ae.EvaluationError):
            ap.parse(bad)


def write(tmp: Path, name: str, body) -> None:
    (tmp / name).write_text(json.dumps(body), encoding="utf8")


def test_the_command_passes_enough_real_evidence_and_names_what_may_be_switched_on(
    tmp_path: Path,
) -> None:
    runs, pol = tmp_path / "r", tmp_path / "p.yaml"
    runs.mkdir()
    write(runs, "a.json", GOOD)
    pol.write_text(yaml.safe_dump(POLICY), encoding="utf8")
    report, code = tool().run(runs, pol, [])
    assert code == 0 and report["may_switch_on"] == [
        {"document_type": "GB-PASSPORT", "check": "TEMPLATE"}
    ]


def test_an_accepted_attack_or_an_untested_wanted_pair_exits_non_zero(tmp_path: Path) -> None:
    runs, pol = tmp_path / "r", tmp_path / "p.yaml"
    runs.mkdir()
    write(runs, "a.json", [*GOOD[:3], row(genuine=False, attack_kind="TEXT_EDIT", verdict="PASS")])
    pol.write_text(yaml.safe_dump(POLICY), encoding="utf8")
    report, code = tool().run(runs, pol, [])
    assert (
        code == 1
        and report["pairs"][0]["status"] == "FAIL"
        and "ATTACKS_ACCEPTED_ABOVE_LIMIT" in report["pairs"][0]["reasons"]
        and report["may_switch_on"] == []
    )
    write(runs, "a.json", GOOD)
    report, code = tool().run(runs, pol, [("FR-ID", "TAMPER")])
    assert code == 1 and any(
        p["document_type"] == "FR-ID" and p["status"] == "FAIL" for p in report["pairs"]
    )  # a pair nobody tested is not a pass


def test_with_no_policy_the_command_says_not_configured_and_a_bad_file_stops_everything(
    tmp_path: Path,
) -> None:
    runs = tmp_path / "r"
    runs.mkdir()
    write(runs, "a.json", GOOD)
    report, code = tool().run(runs, None, [])
    assert (
        code == 2
        and report["pairs"][0]["status"] == "NOT_CONFIGURED"
        and report["may_switch_on"] == []
    )
    write(runs, "bad.json", [{"document_type": "x"}])
    report, code = tool().run(runs, None, [])
    assert code == 3 and "bad.json" in str(report["error"])
