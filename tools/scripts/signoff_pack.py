"""Build the sign-off pack for one specification: its acceptance tests, the evidence behind each, and the approvals still needed.

    backend/.venv/Scripts/python tools/scripts/signoff_pack.py ZID-MACHINE-001 \\
        --leads "Agent Platform,Identity,Security,Platform/SRE,DevSecOps,Authorization" \\
        --out docs/signoff/machine-identity-signoff.md

The pack is a checklist for the people who approve, not an approval. It reads `docs/traceability/acceptance-tests.csv`,
checks that every evidence link (`path::test_function`) points at a file and a function that exist, and leaves one
signature line per approving lead empty. A row without evidence, or whose evidence does not resolve, is listed as a
blocker. Nothing here marks a test done: only the approvers do, by changing the status in the traceability file."""

from __future__ import annotations

import argparse
import csv
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CSV = ROOT / "docs" / "traceability" / "acceptance-tests.csv"


def evidence_resolves(link: str) -> bool:
    if not link:
        return False
    path, _, func = link.partition("::")
    f = ROOT / path
    if not f.is_file():
        return False
    return not func or re.search(rf"\bdef {re.escape(func)}\b", f.read_text(encoding="utf-8")) is not None


def measured_against(link: str) -> str | None:
    """A measurement document (RESULTS.md) that says its gate is not met makes the row a blocker, whatever its status."""
    path = link.partition("::")[0]
    if not path.endswith("RESULTS.md") or not (ROOT / path).is_file():
        return None
    text = (ROOT / path).read_text(encoding="utf-8")
    return f"{path} records that the gate is not met" if re.search(r"gate is \*{0,2}NOT met", text) else None


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("spec")
    p.add_argument("--leads", required=True, help="comma-separated approving leads, from the specification's approval gate")
    p.add_argument("--out", required=True)
    args = p.parse_args()
    rows = [r for r in csv.DictReader(CSV.open(encoding="utf-8")) if r["source_spec"] == args.spec]
    if not rows:
        print(f"no acceptance tests for {args.spec}", file=sys.stderr)
        return 1
    reasons: dict[str, str] = {}
    for r in rows:
        if r["test_status"] == "NOT_STARTED":
            reasons[r["test_id"]] = "not started"
        elif not evidence_resolves(r["evidence_link"]):
            reasons[r["test_id"]] = "evidence is missing or does not resolve"
        elif (why := measured_against(r["evidence_link"])) is not None:
            reasons[r["test_id"]] = why
    blockers = [r for r in rows if r["test_id"] in reasons]
    lines = [
        f"# Sign-off pack: {args.spec}",
        "",
        f"Generated {date.today().isoformat()} by `tools/scripts/signoff_pack.py` from `docs/traceability/acceptance-tests.csv`. "
        "This is a checklist for the approvers. It does not approve anything: a test is done only when an approver changes its status "
        "in the traceability file.",
        "",
        f"- Acceptance tests: **{len(rows)}**; blockers: **{len(blockers)}** (not started, no resolving evidence, or a measurement that says its gate is not met)",
        "",
        "## Tests and evidence",
        "",
        "| ID | Test | Status | Evidence | Resolves |",
        "|---|---|---|---|---|",
    ]
    for r in rows:
        ok = evidence_resolves(r["evidence_link"])
        ev = f"`{r['evidence_link']}`" if r["evidence_link"] else "none"
        lines.append(f"| {r['test_id']} | {r['name']} | {r['test_status']} | {ev} | {'yes' if ok else 'NO'} |")
    lines += ["", "## Blockers", ""]
    if blockers:
        lines += [f"- **{r['test_id']}** {r['name']} ({reasons[r['test_id']]}): {r['pass_condition']}" for r in blockers]
    else:
        lines.append("None.")
    lines += ["", "## Approvals", "", "| Lead | Name | Date | Decision |", "|---|---|---|---|"]
    lines += [f"| {lead.strip()} |  |  |  |" for lead in args.leads.split(",")]
    lines.append("")
    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {out.relative_to(ROOT)}: {len(rows)} tests, {len(blockers)} blockers")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
