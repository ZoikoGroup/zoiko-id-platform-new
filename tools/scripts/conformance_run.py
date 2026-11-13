"""Run the ZID-RCG-003 acceptance cases (section 12) and write the run record (CR-083).

    cd backend
    python -m uv run python ../tools/scripts/conformance_run.py --environment local --out conformance-run.json

Runs exactly the tests the conformance matrix (backend/config/regulatory/conformance-matrix.yaml) names, with the same flags as CI, maps each result
back to its case and writes the body of POST /v1/regulatory/conformance-runs. Nothing is posted or stored here: an operator with
`regulatory.conformance.record` records it, and a different person with `regulatory.conformance.review` accepts or rejects it.

A working tree with uncommitted changes is not the commit it names: the environment is then marked `+dirty`, so nobody mistakes the run for evidence
about the commit alone. A database test needs ZID_TEST_PG_URL, as for the test suite.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "backend"


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=BACKEND, check=True, capture_output=True, text=True
    ).stdout.strip()


def _outcomes(junit: Path, tests: list[str]) -> dict[str, bool]:
    """matrix test id (path.py::function) -> passed. pytest names the module relative to its own root (`tests.unit.test_x`), the matrix
    relative to `backend/` (`app/tests/unit/test_x.py`): they are matched by the end of the path. A parametrized test passes only if every
    parameter passed; a skipped test did not pass."""
    by_end: dict[tuple[str, str], str] = {}
    for t in tests:
        file, func = t.split("::")
        by_end[(file, func)] = t
    out: dict[str, bool] = {}
    for case in ET.parse(junit).getroot().iter("testcase"):
        rel = case.get("classname", "").replace(".", "/") + ".py"
        func = case.get("name", "").split("[", 1)[0]
        match = next(
            (
                t
                for (file, f), t in by_end.items()
                if f == func and (file == rel or file.endswith("/" + rel))
            ),
            None,
        )
        if match is None:
            continue
        ok = not any(child.tag in ("failure", "error", "skipped") for child in case)
        out[match] = out.get(match, True) and ok
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--environment", required=True, help="where it ran: local, ci, staging")
    parser.add_argument("--out", default="conformance-run.json")
    parser.add_argument("--run-id", default=None)
    args = parser.parse_args()

    sys.path.insert(0, str(BACKEND / "app" / "src"))
    from zid_platform.modules.verification.domain import conformance as cf
    from zid_platform.modules.verification.infrastructure.conformance_matrix import (
        conformance_matrix,
    )

    entries = conformance_matrix()
    tests = sorted({t for e in entries for t in e.tests})
    commit = _git("rev-parse", "HEAD")
    dirty = bool(_git("status", "--porcelain"))
    environment = args.environment + ("+dirty" if dirty else "")
    started = datetime.now(UTC)
    with tempfile.TemporaryDirectory() as tmp:
        junit = Path(tmp) / "junit.xml"
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-p",
                "no:logging",
                "-q",
                "--no-header",
                f"--junitxml={junit}",
                *tests,
            ],
            cwd=BACKEND,
            check=False,  # a failing test is a FAIL in the record, not a crash of the runner
        )
        ran = _outcomes(junit, tests) if junit.exists() else {}
    finished = datetime.now(UTC)
    results = cf.results(entries, ran)
    run_id = args.run_id or f"run-{finished.strftime('%Y%m%d-%H%M%S')}-{commit[:8]}"
    body = {
        "run_id": run_id,
        "commit": commit,
        "environment": environment,
        "matrix_hash": cf.matrix_hash(entries),
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "results": [
            {
                "case": r.case,
                "outcome": r.outcome.value,
                "passed": list(r.passed),
                "failed": list(r.failed),
                "not_run": list(r.not_run),
            }
            for r in results
        ],
    }
    Path(args.out).write_text(json.dumps(body, indent=2), encoding="utf-8")
    counts = {o.value: sum(1 for r in results if r.outcome is o) for o in cf.Outcome}
    print(f"run {run_id} on {commit[:12]} ({environment}): {counts}")
    for r in results:
        if r.outcome is not cf.Outcome.PASS:
            print(
                f"  {r.case} {r.outcome.value}"
                + (f" failed: {', '.join(r.failed)}" if r.failed else "")
            )
    print(f"written to {args.out}; record it with POST /v1/regulatory/conformance-runs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
