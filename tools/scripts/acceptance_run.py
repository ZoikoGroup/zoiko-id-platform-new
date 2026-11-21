"""Run the acceptance rows of one or more specifications and write a run record someone else can repeat and compare.

    cd backend
    python -m uv run python ../tools/scripts/acceptance_run.py --environment local --out acceptance-run.json
    python -m uv run python ../tools/scripts/acceptance_run.py ZID-AG-WF-001 CR-093 --environment ci --markdown run.md

It reads `docs/traceability/acceptance-tests.csv`, runs exactly the test each row links (`path::function`) with the same flags as CI, and records for every row
PASS, FAIL, NOT_RUN (the test did not report) or NOT_AUTOMATED (the row's evidence is not a test: a measurement, a document, nothing yet). The record names the
commit, whether the working tree was clean (a dirty tree is marked `+dirty`: it is not the commit it names), the interpreter and platform, and a hash of the rows
it ran, so a second run by another person is comparable row for row.

It decides nothing. A passing row here is engineering's own run, not an independent one, and the record says so (`independent: false` until a different person
supplies `--executed-by` and runs it themselves). It never edits the traceability file: only the approvers change a row's status. Component tests need
ZID_TEST_PG_URL, as for the test suite.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import platform
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
CSV = ROOT / "docs" / "traceability" / "acceptance-tests.csv"
DEFAULT_SPECS = ("ZID-AG-WF-001", "ZID-AG-WF-002", "CR-093", "CR-094", "ZID-MACHINE-001")

PASS, FAIL, NOT_RUN, NOT_AUTOMATED = "PASS", "FAIL", "NOT_RUN", "NOT_AUTOMATED"


def test_target(evidence: str) -> str | None:
    """The pytest target of a row's evidence, relative to `backend/`, or None when the evidence is not a test function (a document, a measurement, a URL)."""
    if not evidence or evidence.startswith(("http://", "https://")) or "::" not in evidence:
        return None
    path, func = evidence.split("::", 1)
    if not path.endswith(".py") or not func.strip():
        return None
    return (path.removeprefix("backend/")) + "::" + func.strip()


def select_rows(rows: list[dict[str, str]], specs: tuple[str, ...]) -> list[dict[str, str]]:
    return [r for r in rows if r["source_spec"] in specs]


def rows_hash(rows: list[dict[str, str]]) -> str:
    """A hash of what was run: the row ids, their specs and their evidence links, in order. A second run is comparable only if this matches."""
    body = json.dumps(
        [
            [r["test_id"], r["source_spec"], r["evidence_link"]]
            for r in sorted(rows, key=lambda r: r["test_id"])
        ],
        separators=(",", ":"),
    )
    return "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()


def outcomes_from_junit(junit: Path, targets: list[str]) -> dict[str, bool]:
    """target (`app/tests/x.py::func`) -> passed. pytest names a module by its dotted path under its own root; a target is matched by the end of its file path.
    A parametrized test passes only if every parameter passed; a skipped test did not pass."""
    by_end = {(t.split("::")[0], t.split("::")[1]): t for t in targets}
    out: dict[str, bool] = {}
    for case in ET.parse(junit).getroot().iter("testcase"):
        rel = case.get("classname", "").replace(".", "/") + ".py"
        func = case.get("name", "").split("[", 1)[0]
        match = next(
            (
                t
                for (file, f), t in by_end.items()
                if f == func
                and (file == rel or file.endswith("/" + rel) or rel.endswith("/" + file))
            ),
            None,
        )
        if match is None:
            continue
        ok = not any(child.tag in ("failure", "error", "skipped") for child in case)
        out[match] = out.get(match, True) and ok
    return out


def results(rows: list[dict[str, str]], ran: dict[str, bool]) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        target = test_target(r["evidence_link"])
        if target is None:
            outcome = NOT_AUTOMATED
        elif target not in ran:
            outcome = NOT_RUN
        else:
            outcome = PASS if ran[target] else FAIL
        out.append(
            {
                "test_id": r["test_id"],
                "source_spec": r["source_spec"],
                "status_in_register": r["test_status"],
                "evidence": r["evidence_link"],
                "outcome": outcome,
            }
        )
    return out


def counts(res: list[dict[str, Any]]) -> dict[str, int]:
    return {
        k: sum(1 for r in res if r["outcome"] == k) for k in (PASS, FAIL, NOT_RUN, NOT_AUTOMATED)
    }


def markdown(body: dict[str, Any]) -> str:
    c = body["counts"]
    lines = [
        f"# Acceptance run {body['run_id']}",
        "",
        f"Commit `{body['commit'][:12]}`, environment `{body['environment']}`, {body['finished_at']}. Rows hash `{body['rows_hash']}`.",
        f"Executed by: {body['executed_by'] or 'engineering'}. Independent: **{'yes' if body['independent'] else 'no'}**.",
        "",
        f"PASS {c[PASS]} · FAIL {c[FAIL]} · NOT_RUN {c[NOT_RUN]} · NOT_AUTOMATED {c[NOT_AUTOMATED]}",
        "",
        "A passing row is this run's result for its linked test. It does not change the row's status in the register: only the approvers do.",
        "",
        "| Row | Spec | Register status | Outcome | Evidence |",
        "|---|---|---|---|---|",
    ]
    for r in body["results"]:
        lines.append(
            f"| {r['test_id']} | {r['source_spec']} | {r['status_in_register']} | {r['outcome']} | `{r['evidence'] or 'none'}` |"
        )
    return "\n".join(lines) + "\n"


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=BACKEND, check=True, capture_output=True, text=True
    ).stdout.strip()


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "specs",
        nargs="*",
        default=list(DEFAULT_SPECS),
        help="source_spec values to run (default: the agent specifications)",
    )
    p.add_argument("--environment", required=True, help="where it ran: local, ci, staging")
    p.add_argument("--out", default="acceptance-run.json")
    p.add_argument("--markdown", default=None, help="also write a readable table to this path")
    p.add_argument("--run-id", default=None)
    p.add_argument(
        "--executed-by",
        default=None,
        help="who ran it. Set it only when someone other than the build team runs it; that is what makes a run independent",
    )
    args = p.parse_args()

    rows = select_rows(
        list(csv.DictReader(CSV.open(encoding="utf-8", newline=""))), tuple(args.specs)
    )
    if not rows:
        print(f"no acceptance rows for {args.specs}", file=sys.stderr)
        return 2
    targets = sorted({t for r in rows if (t := test_target(r["evidence_link"]))})
    commit = _git("rev-parse", "HEAD")
    dirty = bool(_git("status", "--porcelain"))
    started = datetime.now(UTC)
    ran: dict[str, bool] = {}
    if targets:
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
                    *targets,
                ],
                cwd=BACKEND,
                check=False,  # a failing test is a FAIL in the record, not a crash of the runner
            )
            ran = outcomes_from_junit(junit, targets) if junit.exists() else {}
    finished = datetime.now(UTC)
    res = results(rows, ran)
    run_id = args.run_id or f"acc-{finished.strftime('%Y%m%d-%H%M%S')}-{commit[:8]}"
    body: dict[str, Any] = {
        "run_id": run_id,
        "commit": commit,
        "environment": args.environment + ("+dirty" if dirty else ""),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "specs": list(args.specs),
        "rows_hash": rows_hash(rows),
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "executed_by": args.executed_by,
        "independent": bool(args.executed_by),
        "counts": counts(res),
        "results": res,
    }
    Path(args.out).write_text(json.dumps(body, indent=2), encoding="utf-8")
    if args.markdown:
        Path(args.markdown).write_text(markdown(body), encoding="utf-8")
    c = body["counts"]
    print(
        f"run {run_id} on {commit[:12]} ({body['environment']}): PASS {c[PASS]} FAIL {c[FAIL]} NOT_RUN {c[NOT_RUN]} NOT_AUTOMATED {c[NOT_AUTOMATED]}"
    )
    for r in res:
        if r["outcome"] in (FAIL, NOT_RUN):
            print(f"  {r['test_id']} {r['outcome']}: {r['evidence']}")
    print(f"written to {args.out}")
    return 1 if c[FAIL] else 0


if __name__ == "__main__":
    raise SystemExit(main())
