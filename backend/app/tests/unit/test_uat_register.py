"""The UAT register keeps the 104 cases honest (ZID-UAT-005 section 04, 01.2): no PASS without a manifest, no dead developer link, nothing dropped."""

from __future__ import annotations

import csv
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
spec = importlib.util.spec_from_file_location(
    "uat_register", ROOT / "tools" / "scripts" / "uat_register.py"
)
ur = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
sys.modules["uat_register"] = ur
spec.loader.exec_module(ur)  # type: ignore[union-attr]


def rows() -> list[dict[str, str]]:
    return ur.load()


def test_the_shipped_register_holds_the_104_cases_and_passes_its_own_checks() -> None:
    r = rows()
    assert len(r) == 104
    assert {s: sum(1 for x in r if x["suite"] == s) for s in ur.SUITES} == ur.SUITES
    assert ur.problems(r) == []


def test_every_case_is_not_run_and_no_case_has_an_independent_pass_or_a_manifest() -> None:
    """Document 05: every outcome is NOT RUN until independent evidence is attached. This repository holds none."""
    r = rows()
    assert {x["independent_status"] for x in r} == {"NOT_RUN"}
    assert all(not x["manifest_id"].strip() for x in r)
    assert ur.summary(r)["independent_pass"] == 0


def test_a_developer_test_is_evidence_but_never_a_verdict() -> None:
    s = ur.summary(rows())
    assert s["with_developer_evidence"] > 0 and s["independent_pass"] == 0 and s["not_run"] == 104


def test_cases_that_need_a_provider_or_an_audit_say_so() -> None:
    r = {x["case_id"]: x for x in rows()}
    for cid in ("IDV-04", "DOC-05", "RNT-04", "FIN-01", "VC-08", "OPS-04"):
        assert r[cid]["dependency"] == "THIRD_PARTY", cid
    for cid in ("PRI-08", "OPS-05", "OPS-06", "PRI-07"):
        assert r[cid]["dependency"] == "PEOPLE", cid
    assert r["GOV-01"]["dependency"] == "OWN"


def test_a_partial_link_carries_a_note_that_says_what_it_does_not_cover() -> None:
    notes = {x["case_id"]: x["developer_note"] for x in rows() if x["developer_evidence"].strip()}
    for cid in ("IDV-05", "DOC-05", "OPS-06", "PRI-08"):
        assert notes[cid].strip(), f"{cid} is partial and must say how"


def test_the_written_report_is_current() -> None:
    assert ur.REPORT.read_text(encoding="utf-8") == ur.render(rows()), (
        "run tools/scripts/uat_register.py --report"
    )


def mutate(tmp_path: Path, edit) -> list[dict[str, str]]:
    r = [dict(x) for x in rows()]
    edit(r)
    p = tmp_path / "r.csv"
    with p.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=ur.COLUMNS)
        w.writeheader()
        w.writerows(r)
    return ur.load(p)


def test_a_pass_without_a_manifest_a_dropped_case_and_a_dead_link_are_each_caught(
    tmp_path: Path,
) -> None:
    def passes(r):
        r[0]["independent_status"] = "PASS"

    assert any(
        "PASS without an evidence manifest" in p for p in ur.problems(mutate(tmp_path, passes))
    )

    def dropped(r):
        r.pop()

    assert any(
        "103 cases" in p or "suite OPS: 9" in p for p in ur.problems(mutate(tmp_path, dropped))
    )

    def dead(r):
        r[0]["developer_evidence"] = "backend/app/tests/unit/test_nothing_here.py::test_x"

    assert any("does not exist" in p for p in ur.problems(mutate(tmp_path, dead)))

    def renamed(r):
        r[0]["developer_evidence"] = (
            "backend/app/tests/unit/test_documents.py::test_that_was_renamed"
        )

    assert any("has no test named" in p for p in ur.problems(mutate(tmp_path, renamed)))

    def waived_p0(r):
        r[0]["risk"], r[0]["independent_status"] = "P0", "WAIVED"

    assert any("cannot be waived" in p for p in ur.problems(mutate(tmp_path, waived_p0)))


def test_a_manifest_makes_a_pass_acceptable_to_the_check_and_does_not_make_it_true(
    tmp_path: Path,
) -> None:
    def ok(r):
        r[0]["independent_status"], r[0]["manifest_id"] = "PASS", "man_1"

    assert ur.problems(mutate(tmp_path, ok)) == [], (
        "the check enforces the manifest, not the truth of the run: that is the QA lead's signed verdict"
    )
