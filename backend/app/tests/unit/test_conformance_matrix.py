"""The conformance matrix and run rules (ZID-RCG-003 section 12; CR-083): every case mapped, every named test real, honest outcomes, independent review."""

from __future__ import annotations

import ast
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from zid_platform.modules.verification.domain import conformance as cf
from zid_platform.modules.verification.infrastructure.conformance_matrix import conformance_matrix

BACKEND = Path(__file__).resolve().parents[3]
NOW = datetime(2026, 11, 12, 12, tzinfo=UTC)


def _functions(path: Path) -> set[str]:
    return {
        n.name
        for n in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)
    }


def test_the_shipped_matrix_maps_all_48_cases_and_every_named_test_exists() -> None:
    entries = conformance_matrix()
    assert [e.case for e in entries] == list(cf.CASE_IDS)
    missing = []
    for e in entries:
        for test in e.tests:
            file, func = test.split("::")
            path = BACKEND / file
            if not path.exists() or func not in _functions(path):
                missing.append(f"{e.case}: {test}")
    assert not missing, missing


def test_the_matrix_says_where_it_falls_short() -> None:
    entries = conformance_matrix()
    for e in entries:
        if e.coverage is not cf.Coverage.COVERED:
            assert e.note.strip(), e.case


def entry(
    case: str,
    coverage: cf.Coverage = cf.Coverage.COVERED,
    tests: tuple[str, ...] = ("a.py::t",),
    note: str = "",
) -> cf.MatrixEntry:
    return cf.MatrixEntry(
        case,
        "title",
        "expected",
        coverage,
        tests,
        note or ("" if coverage is cf.Coverage.COVERED else "missing x"),
    )


def full(**over: cf.MatrixEntry) -> list[cf.MatrixEntry]:
    return [
        over.get(c, entry(c, tests=(f"app/tests/x.py::test_{c.lower()}",))) for c in cf.CASE_IDS
    ]


def doc(entries: list[cf.MatrixEntry]) -> dict[str, object]:
    return {
        "cases": [
            {
                "case": e.case,
                "title": e.title,
                "expected": e.expected,
                "coverage": e.coverage.value,
                "tests": list(e.tests),
                "note": e.note,
            }
            for e in entries
        ]
    }


def test_a_matrix_with_a_missing_or_duplicate_case_is_refused() -> None:
    entries = full()
    with pytest.raises(cf.ConformanceError) as e:
        cf.parse_matrix(doc(entries[:-1]))
    assert e.value.code == "CONFORMANCE_MATRIX_INCOMPLETE"
    with pytest.raises(cf.ConformanceError):
        cf.parse_matrix(doc([*entries, entries[0]]))


def test_a_covered_case_must_name_a_test_and_a_partial_one_must_say_what_is_missing() -> None:
    with pytest.raises(cf.ConformanceError):
        cf.parse_matrix(doc(full(T05=entry("T05", tests=()))))
    with pytest.raises(cf.ConformanceError):
        cf.parse_matrix(
            doc(
                full(
                    T05=cf.MatrixEntry(
                        "T05", "t", "e", cf.Coverage.PARTIAL, ("app/tests/x.py::test_a",), ""
                    )
                )
            )
        )
    with pytest.raises(cf.ConformanceError):
        cf.parse_matrix(doc(full(T05=entry("T05", tests=("not a test id",)))))


def test_outcomes_are_never_better_than_the_evidence() -> None:
    entries = cf.parse_matrix(
        doc(
            full(
                T02=entry("T02", tests=("app/tests/x.py::test_t02", "app/tests/x.py::test_t02b")),
                T03=entry("T03", cf.Coverage.PARTIAL, ("app/tests/x.py::test_t03",)),
                T04=entry("T04", cf.Coverage.GAP, ()),
            )
        )
    )
    ran = {t: True for e in entries for t in e.tests}
    ran["app/tests/x.py::test_t02b"] = False  # one of two tests failed
    del ran["app/tests/x.py::test_t05"]  # did not run
    by = {r.case: r for r in cf.results(entries, ran)}
    assert by["T01"].outcome is cf.Outcome.PASS
    assert by["T02"].outcome is cf.Outcome.FAIL and by["T02"].failed == (
        "app/tests/x.py::test_t02b",
    )
    assert (
        by["T03"].outcome is cf.Outcome.PARTIAL
    )  # it ran and passed, but does not prove the whole result
    assert by["T04"].outcome is cf.Outcome.GAP
    assert by["T05"].outcome is cf.Outcome.NOT_RUN


def _run(results: tuple[cf.CaseResult, ...]) -> cf.Run:
    run = cf.Run(
        "run-1", "a" * 40, "ci", "b" * 64, NOW - timedelta(minutes=5), NOW, "runner", results
    )
    return replace(run, run_hash=cf.run_hash(run))


def test_the_p0_exit_needs_every_case_passing_and_an_independent_acceptance() -> None:
    passing = tuple(cf.CaseResult(c, cf.Outcome.PASS) for c in cf.CASE_IDS)
    run = _run(passing)
    assert cf.exit_status(run, []).reasons == ("NOT_INDEPENDENTLY_ACCEPTED",)
    accepted = cf.Review("run-1", "reviewer", run.run_hash, cf.Decision.ACCEPTED, "QA-1", NOW)
    assert cf.exit_status(run, [accepted]).met
    rejected = cf.Review("run-1", "other", run.run_hash, cf.Decision.REJECTED, "QA-2", NOW)
    assert "RUN_REJECTED" in cf.exit_status(run, [accepted, rejected]).reasons
    one_partial = tuple(
        cf.CaseResult(c, cf.Outcome.PARTIAL if c == "T45" else cf.Outcome.PASS) for c in cf.CASE_IDS
    )
    assert cf.exit_status(_run(one_partial), [accepted]).reasons == ("CASES_NOT_PASSING:1",)
    assert cf.exit_status(None, []).reasons == ("NO_RUN",)


def test_a_review_is_independent_signs_the_exact_hash_and_is_given_once() -> None:
    run = _run(tuple(cf.CaseResult(c, cf.Outcome.PASS) for c in cf.CASE_IDS))
    good = cf.Review("run-1", "reviewer", run.run_hash, cf.Decision.ACCEPTED, "QA-1", NOW)
    cf.check_review(good, run)
    for bad, code in (
        (replace(good, reviewer="runner"), "CONFORMANCE_REVIEWER_NOT_INDEPENDENT"),
        (replace(good, signed_hash="c" * 64), "CONFORMANCE_HASH_MISMATCH"),
        (replace(good, note_ref=" "), "CONFORMANCE_REVIEW_INVALID"),
    ):
        with pytest.raises(cf.ConformanceError) as e:
            cf.check_review(bad, run)
        assert e.value.code == code
    with pytest.raises(cf.ConformanceError) as e:
        cf.check_review(good, run, [good])
    assert e.value.code == "CONFORMANCE_ALREADY_REVIEWED"


def test_a_run_must_report_every_case_name_a_full_commit_and_keep_its_hash() -> None:
    run = _run(tuple(cf.CaseResult(c, cf.Outcome.PASS) for c in cf.CASE_IDS))
    cf.check_run(run)
    for bad, code in (
        (replace(run, commit="abc"), "CONFORMANCE_COMMIT_INVALID"),
        (replace(run, results=run.results[:-1]), "CONFORMANCE_RUN_INCOMPLETE"),
        (
            replace(run, environment="ci+dirty"),
            "CONFORMANCE_HASH_MISMATCH",
        ),  # changed after sealing
    ):
        with pytest.raises(cf.ConformanceError) as e:
            cf.check_run(bad)
        assert e.value.code == code
