"""tools/scripts/acceptance_run.py: the record of an acceptance run is exact about what ran, and never claims more than it did."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[4] / "tools" / "scripts" / "acceptance_run.py"
spec = importlib.util.spec_from_file_location("acceptance_run", SCRIPT)
assert spec and spec.loader
ar = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ar)

ROWS = [
    {
        "test_id": "A-1",
        "source_spec": "S1",
        "test_status": "IN_PROGRESS",
        "evidence_link": "backend/app/tests/unit/test_x.py::test_one",
    },
    {
        "test_id": "A-2",
        "source_spec": "S1",
        "test_status": "IN_PROGRESS",
        "evidence_link": "backend/app/tests/component/test_y_pg.py::test_two",
    },
    {
        "test_id": "A-3",
        "source_spec": "S1",
        "test_status": "IN_PROGRESS",
        "evidence_link": "tests/load/machine-token/RESULTS.md",
    },
    {"test_id": "A-4", "source_spec": "S1", "test_status": "NOT_STARTED", "evidence_link": ""},
    {
        "test_id": "B-1",
        "source_spec": "S2",
        "test_status": "IN_PROGRESS",
        "evidence_link": "https://example.test/report",
    },
]


@pytest.mark.parametrize(
    ("evidence", "target"),
    [
        ("backend/app/tests/unit/test_x.py::test_one", "app/tests/unit/test_x.py::test_one"),
        ("app/tests/unit/test_x.py::test_one", "app/tests/unit/test_x.py::test_one"),
        ("tests/load/machine-token/RESULTS.md", None),  # a document, not a test
        ("", None),
        ("https://example.test/report", None),
        ("backend/app/tests/unit/test_x.py::", None),  # no function named
        ("docs/x.md::heading", None),  # not a python test
    ],
)
def test_only_a_linked_test_function_is_run(evidence: str, target: str | None) -> None:
    assert ar.test_target(evidence) == target


def test_rows_are_selected_by_specification_and_hashed_by_what_they_link() -> None:
    chosen = ar.select_rows(ROWS, ("S1",))
    assert [r["test_id"] for r in chosen] == ["A-1", "A-2", "A-3", "A-4"]
    h = ar.rows_hash(chosen)
    assert h == ar.rows_hash(
        list(reversed(chosen))
    )  # the order the file lists them in does not matter
    changed = [dict(r) for r in chosen]
    changed[0]["evidence_link"] = "backend/app/tests/unit/test_x.py::test_other"
    assert ar.rows_hash(changed) != h  # a different link is a different run
    status_only = [dict(r, test_status="VERIFIED") for r in chosen]
    assert ar.rows_hash(status_only) == h  # a status change does not change what was run


def test_a_junit_report_is_matched_to_targets_and_a_failed_or_skipped_parameter_fails_the_row(
    tmp_path: Path,
) -> None:
    junit = tmp_path / "junit.xml"
    junit.write_text(
        """<testsuites><testsuite>
        <testcase classname="tests.unit.test_x" name="test_one"/>
        <testcase classname="tests.component.test_y_pg" name="test_two[a]"/>
        <testcase classname="tests.component.test_y_pg" name="test_two[b]"><failure message="no"/></testcase>
        <testcase classname="tests.unit.test_z" name="test_skipped"><skipped/></testcase>
        <testcase classname="tests.unit.test_unrelated" name="test_nobody_asked"/>
        </testsuite></testsuites>""",
        encoding="utf-8",
    )
    targets = [
        "app/tests/unit/test_x.py::test_one",
        "app/tests/component/test_y_pg.py::test_two",
        "app/tests/unit/test_z.py::test_skipped",
    ]
    got = ar.outcomes_from_junit(junit, targets)
    assert got == {
        targets[0]: True,
        targets[1]: False,
        targets[2]: False,
    }  # one failed parameter fails the test; a skip is not a pass


def test_each_row_is_pass_fail_not_run_or_not_automated_and_never_more() -> None:
    chosen = ar.select_rows(ROWS, ("S1",))
    ran = {"app/tests/unit/test_x.py::test_one": True}
    res = {r["test_id"]: r["outcome"] for r in ar.results(chosen, ran)}
    assert res == {
        "A-1": ar.PASS,
        "A-2": ar.NOT_RUN,
        "A-3": ar.NOT_AUTOMATED,
        "A-4": ar.NOT_AUTOMATED,
    }
    failed = {
        r["test_id"]: r["outcome"]
        for r in ar.results(chosen, {"app/tests/unit/test_x.py::test_one": False})
    }
    assert failed["A-1"] == ar.FAIL
    assert ar.counts(ar.results(chosen, ran)) == {
        ar.PASS: 1,
        ar.FAIL: 0,
        ar.NOT_RUN: 1,
        ar.NOT_AUTOMATED: 2,
    }


def test_the_register_status_is_reported_beside_the_outcome_and_the_run_is_not_independent_by_default() -> (
    None
):
    res = ar.results(ar.select_rows(ROWS, ("S1",)), {"app/tests/unit/test_x.py::test_one": True})
    assert (
        res[0]["status_in_register"] == "IN_PROGRESS" and res[0]["outcome"] == ar.PASS
    )  # a pass here does not move the register
    body = {
        "run_id": "r", "commit": "c" * 40, "environment": "local", "finished_at": "now", "rows_hash": "sha256:x",
        "executed_by": None, "independent": False, "counts": ar.counts(res), "results": res,
    }  # fmt: skip
    text = ar.markdown(body)
    assert (
        "Independent: **no**" in text and "does not change the row's status in the register" in text
    )
