"""Independent conformance runs (ZID-RCG-003 section 12 and 12.1; CR-083). Pure domain, no I/O.

Section 12: execute all 48 cases; each test stores the code commit, environment, test run, actual trace, expected results and independent reviewer.
Section 12.1 P0 exit: all 48 required tests executed with signed evidence and no open critical defect. Rules, each with a test:
- the matrix maps every case T01..T48 to the tests that prove it, as COVERED (they prove the mandatory result), PARTIAL (they prove part of it,
  and the note says what is missing) or GAP (nothing proves it yet). A case missing from the matrix, or a COVERED case with no test, is refused;
- a run is built from what actually ran: a case PASSES only when it is COVERED and every one of its tests passed; a failed test FAILS the case; a
  test that did not run makes it NOT_RUN; PARTIAL and GAP never pass, whatever ran;
- the run's hash covers the commit, the environment, the matrix hash and every outcome, so a reviewer signs exactly what was run;
- a review is by someone other than whoever recorded the run, signs that hash, and ACCEPTS or REJECTS it;
- the P0 exit is met only when all 48 cases PASS in an ACCEPTED run. Nothing here declares it met otherwise.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

CASE_IDS = tuple(f"T{n:02d}" for n in range(1, 49))
COMMIT = re.compile(r"^[0-9a-f]{40}$")
NODE = re.compile(r"^[A-Za-z0-9_./-]+\.py::[A-Za-z_][A-Za-z0-9_]*(\[[^\]]+\])?$")


class ConformanceError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


class Coverage(StrEnum):
    COVERED = "COVERED"
    PARTIAL = "PARTIAL"
    GAP = "GAP"


class Outcome(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    NOT_RUN = "NOT_RUN"
    PARTIAL = "PARTIAL"  # what ran passed, but the matrix says it does not prove the whole result
    GAP = "GAP"


class Decision(StrEnum):
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"


@dataclass(frozen=True)
class MatrixEntry:
    case: str
    title: str
    expected: str
    coverage: Coverage
    tests: tuple[str, ...] = ()
    note: str = ""


@dataclass(frozen=True)
class CaseResult:
    case: str
    outcome: Outcome
    passed: tuple[str, ...] = ()
    failed: tuple[str, ...] = ()
    not_run: tuple[str, ...] = ()


@dataclass(frozen=True)
class Run:
    run_id: str
    commit: str
    environment: str
    matrix_hash: str
    started_at: datetime
    finished_at: datetime
    recorded_by: str
    results: tuple[CaseResult, ...]
    run_hash: str = ""


@dataclass(frozen=True)
class Review:
    run_id: str
    reviewer: str
    signed_hash: str
    decision: Decision
    note_ref: str
    reviewed_at: datetime


@dataclass(frozen=True)
class ExitStatus:
    met: bool
    reasons: tuple[str, ...]
    counts: Mapping[str, int] = field(default_factory=dict)


def parse_matrix(doc: Mapping[str, Any]) -> list[MatrixEntry]:
    """Every case exactly once; COVERED and PARTIAL name their tests; a test id is `path.py::function`."""
    try:
        entries = [
            MatrixEntry(
                case=str(raw["case"]),
                title=str(raw["title"]),
                expected=str(raw["expected"]),
                coverage=Coverage(str(raw["coverage"]).upper()),
                tests=tuple(str(t) for t in raw.get("tests") or ()),
                note=str(raw.get("note") or ""),
            )
            for raw in doc.get("cases") or []
        ]
    except (KeyError, ValueError, TypeError) as err:
        raise ConformanceError("CONFORMANCE_MATRIX_INVALID", str(err)[:80]) from err
    seen = [e.case for e in entries]
    if sorted(seen) != list(CASE_IDS):
        missing = sorted(set(CASE_IDS) - set(seen))
        extra = sorted({c for c in seen if seen.count(c) > 1} | (set(seen) - set(CASE_IDS)))
        raise ConformanceError(
            "CONFORMANCE_MATRIX_INCOMPLETE", f"missing {missing}, duplicate or unknown {extra}"
        )
    for e in entries:
        if e.coverage is not Coverage.GAP and not e.tests:
            raise ConformanceError(
                "CONFORMANCE_MATRIX_INVALID", f"{e.case} is {e.coverage.value} but names no test"
            )
        if e.coverage is not Coverage.COVERED and not e.note.strip():
            raise ConformanceError(
                "CONFORMANCE_MATRIX_INVALID", f"{e.case} is {e.coverage.value}: say what is missing"
            )
        bad = [t for t in e.tests if not NODE.match(t)]
        if bad:
            raise ConformanceError(
                "CONFORMANCE_MATRIX_INVALID", f"{e.case}: {bad[0]} is not path.py::test"
            )
    return sorted(entries, key=lambda e: e.case)


def matrix_hash(entries: Sequence[MatrixEntry]) -> str:
    body = [
        {"case": e.case, "coverage": e.coverage.value, "tests": sorted(e.tests)}
        for e in sorted(entries, key=lambda e: e.case)
    ]
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def results(entries: Sequence[MatrixEntry], ran: Mapping[str, bool]) -> tuple[CaseResult, ...]:
    """`ran`: test id -> passed. A test the run did not report is NOT_RUN."""
    out: list[CaseResult] = []
    for e in sorted(entries, key=lambda e: e.case):
        passed = tuple(t for t in e.tests if ran.get(t) is True)
        failed = tuple(t for t in e.tests if ran.get(t) is False)
        not_run = tuple(t for t in e.tests if t not in ran)
        if e.coverage is Coverage.GAP:
            outcome = Outcome.GAP
        elif failed:
            outcome = Outcome.FAIL
        elif not_run:
            outcome = Outcome.NOT_RUN
        elif e.coverage is Coverage.PARTIAL:
            outcome = Outcome.PARTIAL
        else:
            outcome = Outcome.PASS
        out.append(CaseResult(e.case, outcome, passed, failed, not_run))
    return tuple(out)


def run_hash(run: Run) -> str:
    body = {
        "run_id": run.run_id,
        "commit": run.commit,
        "environment": run.environment,
        "matrix_hash": run.matrix_hash,
        "started_at": run.started_at.isoformat(),
        "finished_at": run.finished_at.isoformat(),
        "results": [
            {
                "case": r.case,
                "outcome": r.outcome.value,
                "passed": sorted(r.passed),
                "failed": sorted(r.failed),
                "not_run": sorted(r.not_run),
            }
            for r in sorted(run.results, key=lambda r: r.case)
        ],
    }
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def check_run(run: Run) -> None:
    if not COMMIT.match(run.commit):
        raise ConformanceError("CONFORMANCE_COMMIT_INVALID", "the full 40-character commit")
    if not run.environment.strip() or not run.recorded_by.strip():
        raise ConformanceError("CONFORMANCE_RUN_INVALID", "environment and recorder are required")
    if run.finished_at < run.started_at:
        raise ConformanceError("CONFORMANCE_RUN_INVALID", "finished before it started")
    if sorted(r.case for r in run.results) != list(CASE_IDS):
        raise ConformanceError("CONFORMANCE_RUN_INCOMPLETE", "a run reports every case, T01 to T48")
    if run.run_hash and run.run_hash != run_hash(run):
        raise ConformanceError("CONFORMANCE_HASH_MISMATCH")


def check_review(review: Review, run: Run, previous: Iterable[Review] = ()) -> None:
    if review.reviewer == run.recorded_by:
        raise ConformanceError(
            "CONFORMANCE_REVIEWER_NOT_INDEPENDENT", "whoever recorded the run cannot review it"
        )
    if review.signed_hash != run.run_hash:
        raise ConformanceError("CONFORMANCE_HASH_MISMATCH", "sign the hash of the run you reviewed")
    if any(p.reviewer == review.reviewer for p in previous):
        raise ConformanceError("CONFORMANCE_ALREADY_REVIEWED", "one review per reviewer")
    if not review.note_ref.strip():
        raise ConformanceError("CONFORMANCE_REVIEW_INVALID", "name the review record")


def exit_status(run: Run | None, reviews: Iterable[Review]) -> ExitStatus:
    """Section 12.1 P0 exit, for one run: every case PASS, and accepted by an independent reviewer with nobody rejecting it."""
    if run is None:
        return ExitStatus(False, ("NO_RUN",))
    counts = {o.value: sum(1 for r in run.results if r.outcome is o) for o in Outcome}
    reasons: list[str] = []
    if counts[Outcome.PASS.value] != len(CASE_IDS):
        reasons.append(f"CASES_NOT_PASSING:{len(CASE_IDS) - counts[Outcome.PASS.value]}")
    rs = list(reviews)
    if any(r.decision is Decision.REJECTED for r in rs):
        reasons.append("RUN_REJECTED")
    if not any(r.decision is Decision.ACCEPTED for r in rs):
        reasons.append("NOT_INDEPENDENTLY_ACCEPTED")
    return ExitStatus(not reasons, tuple(reasons), counts)
