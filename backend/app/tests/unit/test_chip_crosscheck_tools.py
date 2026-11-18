"""The run files, the gate policy file and the lab command for the independent chip cross-check (E03; CR-123)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
import yaml
from zid_platform.modules.evidence.domain import chip_crosscheck as cc
from zid_platform.modules.evidence.infrastructure import crosscheck_policy as policy

REPO = Path(__file__).resolve().parents[4]
SOD, G1 = "a" * 64, "1" * 64


def doc(reader: str = "ZOIKO", **kw) -> dict:
    base = {
        "reader": reader, "reader_version": "1.0", "device_model": "Pixel 8", "os_version": "14", "document_ref": "lab-doc-1", "access": "PACE_GM",
        "read": {"sod_sha256": SOD, "groups": {"1": G1, "2": "2" * 64}},
        "crypto": {"passive_auth": "PASS", "issuer_chain": "TRUSTED", "revocation": "CHECKED_GOOD", "active_auth": "PASS", "chip_auth": "NOT_SUPPORTED"},
    }  # fmt: skip
    base.update(kw)
    return base


def test_a_good_run_file_parses_and_a_reader_name_is_normalised() -> None:
    r = cc.parse_run(doc("jmrtd"))
    assert (
        r.reader == "JMRTD"
        and r.read.groups == {1: G1, 2: "2" * 64}
        and r.crypto.active_auth == "PASS"
    )


@pytest.mark.parametrize(
    "bad",
    [
        {**doc(), "surprise": 1},
        {k: v for k, v in doc().items() if k != "device_model"},
        doc(read={"sod_sha256": "xyz", "groups": {}}),
        doc(read={"sod_sha256": SOD, "groups": {"99": G1}}),
        doc(read={"sod_sha256": SOD, "groups": {"1": "nothex"}}),
        doc(crypto={"passive_auth": "MAYBE"}),
        doc(crypto={"surprise": "PASS"}),
        doc(read={"sod_sha256": SOD}),
    ],
)
def test_a_malformed_run_file_is_refused_whole(bad: dict) -> None:
    with pytest.raises(cc.CrosscheckError):
        cc.parse_run(bad)


def test_runs_are_paired_by_document_device_and_access_and_the_unpaired_are_named() -> None:
    runs = [
        cc.parse_run(d)
        for d in (doc("ZOIKO"), doc("JMRTD"), doc("NFCPASSPORTREADER", device_model="iPhone 15"))
    ]
    comparisons, unpaired = cc.pair_runs(runs)
    assert [c.reference for c in comparisons] == ["JMRTD"] and unpaired == [
        "NFCPASSPORTREADER:lab-doc-1:iPhone 15:PACE_GM"
    ]


def test_the_shipped_policy_file_records_none_so_the_gate_cannot_pass() -> None:
    assert policy.load() is None
    assert cc.gate([], policy.load()).status is cc.Status.NOT_CONFIGURED


def test_a_policy_is_read_strictly() -> None:
    p = policy.parse(
        {
            "policy": {
                "min_documents": 3,
                "min_devices": 2,
                "required_access": ["bac", "pace_gm"],
                "min_runs_per_access": 2,
                "required_references": ["jmrtd"],
            }
        }
    )
    assert (
        p is not None
        and p.required_access == frozenset({"BAC", "PACE_GM"})
        and p.min_comparable_passive_auth_share == 1.0
    )
    for bad in (
        {
            "policy": {
                "min_documents": 0,
                "min_devices": 1,
                "required_access": ["BAC"],
                "min_runs_per_access": 1,
                "required_references": ["JMRTD"],
            }
        },
        {
            "policy": {
                "min_documents": 1,
                "min_devices": 1,
                "required_access": ["TELEPATHY"],
                "min_runs_per_access": 1,
                "required_references": ["JMRTD"],
            }
        },
        {
            "policy": {
                "min_documents": 1,
                "min_devices": 1,
                "required_access": ["BAC"],
                "min_runs_per_access": 1,
                "required_references": ["ZOIKO"],
            }
        },
        {
            "policy": {
                "min_documents": 1,
                "min_devices": 1,
                "required_access": ["BAC"],
                "min_runs_per_access": 1,
            }
        },
        {
            "policy": {
                "min_documents": 1,
                "min_devices": 1,
                "required_access": ["BAC"],
                "min_runs_per_access": 1,
                "required_references": ["JMRTD"],
                "extra": 1,
            }
        },
        {
            "policy": {
                "min_documents": True,
                "min_devices": 1,
                "required_access": ["BAC"],
                "min_runs_per_access": 1,
                "required_references": ["JMRTD"],
            }
        },
    ):
        with pytest.raises(cc.CrosscheckError):
            policy.parse(bad)


def tool():
    spec = importlib.util.spec_from_file_location(
        "chip_crosscheck_cli", REPO / "tools" / "scripts" / "chip_crosscheck.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write(tmp: Path, name: str, body: dict) -> None:
    (tmp / name).write_text(json.dumps(body), encoding="utf8")


POLICY_DOC = {
    "policy": {
        "min_documents": 1,
        "min_devices": 1,
        "required_access": ["PACE_GM"],
        "min_runs_per_access": 1,
        "required_references": ["JMRTD"],
    }
}


def test_the_lab_command_passes_agreeing_runs_and_exits_zero_only_then(tmp_path: Path) -> None:
    runs, pol = tmp_path / "runs", tmp_path / "policy.yaml"
    runs.mkdir()
    write(runs, "ours.json", doc("ZOIKO"))
    write(runs, "ref.json", doc("JMRTD"))
    pol.write_text(yaml.safe_dump(POLICY_DOC), encoding="utf8")
    report, code = tool().run(runs, pol)
    assert (
        code == 0
        and report["gate"] == "PASS"
        and report["unpaired"] == []
        and report["counts"]["comparable"] == 1
    )


def test_a_critical_disagreement_exits_non_zero_and_names_it(tmp_path: Path) -> None:
    runs, pol = tmp_path / "runs", tmp_path / "policy.yaml"
    runs.mkdir()
    write(runs, "ours.json", doc("ZOIKO"))
    write(runs, "ref.json", doc("JMRTD", crypto={**doc()["crypto"], "passive_auth": "FAIL"}))
    pol.write_text(yaml.safe_dump(POLICY_DOC), encoding="utf8")
    report, code = tool().run(runs, pol)
    assert code == 1 and report["gate"] == "FAIL" and "CRITICAL_DISAGREEMENT" in report["reasons"]
    assert report["comparisons"][0]["critical"] == ["passive_auth"]


def test_with_no_policy_the_command_reports_not_configured_and_a_bad_file_stops_everything(
    tmp_path: Path,
) -> None:
    runs = tmp_path / "runs"
    runs.mkdir()
    write(runs, "ours.json", doc("ZOIKO"))
    write(runs, "ref.json", doc("JMRTD"))
    report, code = tool().run(runs, None)
    assert code == 2 and report["gate"] == "NOT_CONFIGURED"
    write(runs, "bad.json", {"reader": "ZOIKO"})
    report, code = tool().run(runs, None)
    assert code == 3 and "bad.json" in str(report["error"])
