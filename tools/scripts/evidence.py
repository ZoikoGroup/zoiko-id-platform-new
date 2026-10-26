"""Evidence index: turn test results into retained evidence for the traceability matrix (ZID-VERIFY-MASTER-001
MPR-01: "every P0 requirement has an owner, a test ID, the latest result and retained evidence").

    python tools/scripts/evidence.py --run          # run the backend suites, then write the index
    python tools/scripts/evidence.py --junit x.xml  # index an existing JUnit report (a CI artifact)
    python tools/scripts/evidence.py --check        # exit 1 if any linked evidence test failed or vanished

It reads every `evidence_link` of the form `path::test_function` in docs/traceability/acceptance-tests.csv and
requirements.csv, looks the test up in the JUnit report, and writes docs/traceability/evidence-results.csv:
the row, its link, the latest result (PASSED / FAILED / SKIPPED / NOT_RUN), when, and at which commit.

It never changes a status. Whether a passing test is *enough* to call a row VERIFIED is a reviewer's decision,
recorded in the CSV by hand; this index only makes the evidence current, reproducible and checkable. The
`--check` mode keeps it honest in both directions: a row claiming progress whose evidence test now fails, or
no longer exists, fails the build.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TRACE = ROOT / "docs" / "traceability"
OUT = TRACE / "evidence-results.csv"
SOURCES = (
    ("acceptance", TRACE / "acceptance-tests.csv", "test_id", "test_status"),
    ("requirement", TRACE / "requirements.csv", "requirement_id", "implementation_status"),
)
FIELDS = ["kind", "id", "status", "evidence_link", "result", "run_at", "commit"]


def run_backend(junit: Path) -> int:
    """The backend suites (unit, contract, property, component). Component tests need ZID_TEST_PG_URL."""
    py = ROOT / "backend" / ".venv" / ("Scripts" if sys.platform == "win32" else "bin") / "python"
    cmd = [
        str(py if py.exists() else sys.executable),
        "-m",
        "pytest",
        "app",
        "libs",
        "-q",
        f"--junitxml={junit}",
        "-p",
        "no:cacheprovider",
    ]
    return subprocess.run(cmd, cwd=ROOT / "backend", check=False).returncode


def load_junit(path: Path) -> dict[str, str]:
    """`backend/<dir>/<file>.py::<test>` -> PASSED | FAILED | SKIPPED, from a pytest JUnit report.

    A parametrized test is FAILED if any case failed, PASSED if at least one passed and none failed."""
    seen: dict[str, set[str]] = {}
    for case in ET.parse(path).getroot().iter("testcase"):
        # classname is the dotted module path, e.g. app.tests.component.test_x
        key = f"backend/{case.get('classname', '').replace('.', '/')}.py::{case.get('name', '').split('[')[0]}"
        if case.find("failure") is not None or case.find("error") is not None:
            outcome = "FAILED"
        elif case.find("skipped") is not None:
            outcome = "SKIPPED"
        else:
            outcome = "PASSED"
        seen.setdefault(key, set()).add(outcome)
    return {
        key: "FAILED" if "FAILED" in o else "PASSED" if "PASSED" in o else "SKIPPED"
        for key, o in seen.items()
    }


def commit() -> str:
    out = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return out.stdout.strip() or "unknown"


def index(results: dict[str, str]) -> list[dict[str, str]]:
    now, sha = dt.datetime.now(dt.UTC).isoformat(timespec="seconds"), commit()
    rows: list[dict[str, str]] = []
    for kind, path, id_col, status_col in SOURCES:
        with path.open(encoding="utf-8", newline="") as fh:
            for r in csv.DictReader(fh):
                link = (r.get("evidence_link") or "").strip()
                if not link:
                    continue
                if "::" in link and not link.startswith("http"):
                    result = results.get(
                        link, "NOT_RUN" if (ROOT / link.split("::")[0]).exists() else "MISSING"
                    )
                else:
                    result = "NOT_RUN"  # a path or URL without a test function: reviewed by hand, not executed here
                rows.append(
                    {
                        "kind": kind,
                        "id": r[id_col],
                        "status": r[status_col],
                        "evidence_link": link,
                        "result": result,
                        "run_at": now,
                        "commit": sha,
                    }
                )
    return rows


def write(rows: list[dict[str, str]]) -> None:
    with OUT.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(sorted(rows, key=lambda r: (r["kind"], r["id"])))


def problems(rows: list[dict[str, str]]) -> list[str]:
    bad = []
    for r in rows:
        if r["status"] != "NOT_STARTED" and r["result"] in ("FAILED", "MISSING"):
            bad.append(
                f"{r['kind']} {r['id']} ({r['status']}): evidence {r['evidence_link']} is {r['result']}"
            )
    return bad


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--run", action="store_true", help="run the backend suites first")
    ap.add_argument("--junit", type=Path, help="index this JUnit report")
    ap.add_argument(
        "--check", action="store_true", help="exit 1 if linked evidence failed or vanished"
    )
    args = ap.parse_args()

    junit = args.junit
    if args.run:
        junit = ROOT / "test-results" / "backend-junit.xml"
        junit.parent.mkdir(parents=True, exist_ok=True)
        code = run_backend(junit)
        print(f"backend suites exited with {code}")
    if junit is None or not junit.exists():
        print("no JUnit report: pass --junit <file> or --run", file=sys.stderr)
        return 2
    rows = index(load_junit(junit))
    write(rows)
    tally: dict[str, int] = {}
    for r in rows:
        tally[r["result"]] = tally.get(r["result"], 0) + 1
    print(
        f"indexed {len(rows)} evidence links -> {OUT.relative_to(ROOT)}: {dict(sorted(tally.items()))}"
    )
    bad = problems(rows)
    for b in bad:
        print("PROBLEM:", b)
    return 1 if (args.check and bad) else 0


if __name__ == "__main__":
    raise SystemExit(main())
