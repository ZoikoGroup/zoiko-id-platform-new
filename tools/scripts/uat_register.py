"""The UAT case register for ZID-UAT-005: the 104 mandatory cases, what could run them, and what only independent evidence can settle.

    python tools/scripts/uat_register.py --check          # exit 1 if the register is malformed or claims something it cannot back
    python tools/scripts/uat_register.py --report [PATH]  # write docs/qa/uat-register-report.md
    python tools/scripts/uat_register.py --json           # the summary as JSON

The register (`docs/traceability/uat-cases.csv`) is maintained by the independent QA lead. This tool decides nothing and runs nothing:

- `developer_evidence` is the existing test that exercises the scenario (checked to exist); it is NOT an independent verdict;
- `independent_status` is NOT_RUN until an independent run records a result; PASS needs a `manifest_id` (a screenshot or a developer test is never a PASS);
- `dependency` says what a real run needs: OWN (code and fixtures only), THIRD_PARTY (a provider, register, source or partner) or PEOPLE (an independent audit or exercise);
- `risk` is PROPOSED_* until the QA lead confirms it (P0, P1, P2).

A case can never be dropped, renamed or added without this check failing, so the 104 stay 104.
"""

from __future__ import annotations

import argparse
import csv
import json
import pathlib
import re
import sys
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[2]
REGISTER = ROOT / "docs" / "traceability" / "uat-cases.csv"
REPORT = ROOT / "docs" / "qa" / "uat-register-report.md"
COLUMNS = (
    "case_id", "suite", "risk", "input", "assertion", "proof_needed", "dependency",
    "developer_evidence", "developer_note", "independent_status", "manifest_id",
)  # fmt: skip
SUITES = {
    "GOV": 8,
    "IDV": 10,
    "DOC": 10,
    "EDU": 8,
    "PRO": 9,
    "ORG": 6,
    "FIN": 7,
    "VC": 8,
    "RNT": 8,
    "INT": 10,
    "PRI": 10,
    "OPS": 10,
}
SUITE_NAMES = {
    "GOV": "Tenancy, role, authority and scope",
    "IDV": "Identity, document capture and biometrics",
    "DOC": "Document trust, signatures and issuer evidence",
    "EDU": "Academic and education credentials",
    "PRO": "Professional licenses, insurance and scope",
    "ORG": "Business verification and delegated authority",
    "FIN": "Fraud, bank ownership and AML",
    "VC": "Portable verified credentials and wallets",
    "RNT": "Zoiko Rooms, property and regulated rent",
    "INT": "API, SDK, supplier and event integration",
    "PRI": "Privacy, security, bias and redress",
    "OPS": "Resilience, observability and launch governance",
}
STATUSES = ("NOT_RUN", "BLOCKED", "PASS", "FAIL", "INCONCLUSIVE", "EXPIRED", "WAIVED")
RISKS = ("PROPOSED_P0", "PROPOSED_P1", "P0", "P1", "P2")
DEPENDENCIES = ("OWN", "THIRD_PARTY", "PEOPLE")
ID = re.compile(r"^([A-Z]{2,3})-(\d{2})$")


def load(path: pathlib.Path = REGISTER) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _test_exists(link: str, root: pathlib.Path) -> str | None:
    """None when the link resolves, else why not. A link is `path` or `path::test_function`."""
    path, _, name = link.partition("::")
    file = root / path
    if not file.is_file():
        return f"{path} does not exist"
    if name and not re.search(
        rf"^\s*(?:async )?def {re.escape(name)}\(",
        file.read_text(encoding="utf-8", errors="ignore"),
        re.MULTILINE,
    ):
        return f"{path} has no test named {name}"
    return None


def problems(rows: list[dict[str, str]], root: pathlib.Path = ROOT) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for r in rows:
        cid = r.get("case_id", "")
        m = ID.match(cid)
        if not m or m.group(1) not in SUITES:
            out.append(f"{cid!r}: not a case id of a known suite")
            continue
        if cid in seen:
            out.append(f"{cid}: listed twice")
        seen.add(cid)
        if r["suite"] != m.group(1):
            out.append(f"{cid}: suite {r['suite']} does not match its id")
        if r["risk"] not in RISKS:
            out.append(f"{cid}: risk {r['risk']!r} is not one of {RISKS}")
        if r["dependency"] not in DEPENDENCIES:
            out.append(
                f"{cid}: dependency {r['dependency']!r} is not one of {DEPENDENCIES}"
            )
        if r["independent_status"] not in STATUSES:
            out.append(
                f"{cid}: status {r['independent_status']!r} is not one of {STATUSES}"
            )
        if r["independent_status"] == "PASS" and not r["manifest_id"].strip():
            out.append(
                f"{cid}: PASS without an evidence manifest is INCONCLUSIVE, never a PASS"
            )
        if r["independent_status"] == "WAIVED" and r["risk"] in ("P0", "PROPOSED_P0"):
            out.append(f"{cid}: a P0 case cannot be waived")
        if not all(r[c].strip() for c in ("input", "assertion", "proof_needed")):
            out.append(f"{cid}: input, assertion and proof needed are all required")
        if r["developer_evidence"].strip():
            why = _test_exists(r["developer_evidence"].strip(), root)
            if why:
                out.append(f"{cid}: developer evidence {why}")
    for suite, n in SUITES.items():
        have = sum(1 for r in rows if r.get("suite") == suite)
        if have != n:
            out.append(f"suite {suite}: {have} cases, Document 05 lists {n}")
    if len(rows) != sum(SUITES.values()):
        out.append(f"{len(rows)} cases, Document 05 lists {sum(SUITES.values())}")
    return out


def summary(rows: list[dict[str, str]]) -> dict[str, Any]:
    by_suite: dict[str, dict[str, int]] = {}
    for r in rows:
        s = by_suite.setdefault(
            r["suite"],
            {
                "cases": 0,
                "with_developer_evidence": 0,
                "third_party": 0,
                "people": 0,
                "not_run": 0,
            },
        )
        s["cases"] += 1
        s["with_developer_evidence"] += 1 if r["developer_evidence"].strip() else 0
        s["third_party"] += 1 if r["dependency"] == "THIRD_PARTY" else 0
        s["people"] += 1 if r["dependency"] == "PEOPLE" else 0
        s["not_run"] += 1 if r["independent_status"] == "NOT_RUN" else 0
    return {
        "cases": len(rows),
        "with_developer_evidence": sum(
            1 for r in rows if r["developer_evidence"].strip()
        ),
        "partial_or_mechanism": sum(
            1
            for r in rows
            if r["developer_evidence"].strip() and r["developer_note"].strip()
        ),
        "third_party": sum(1 for r in rows if r["dependency"] == "THIRD_PARTY"),
        "people": sum(1 for r in rows if r["dependency"] == "PEOPLE"),
        "independent_pass": sum(1 for r in rows if r["independent_status"] == "PASS"),
        "not_run": sum(1 for r in rows if r["independent_status"] == "NOT_RUN"),
        "proposed_p0": sum(1 for r in rows if r["risk"] == "PROPOSED_P0"),
        "by_suite": by_suite,
    }


def render(rows: list[dict[str, str]]) -> str:
    s = summary(rows)
    out = [
        "# UAT case register report (ZID-UAT-005)",
        "",
        "Generated by `tools/scripts/uat_register.py` from `docs/traceability/uat-cases.csv`. It decides nothing. **Every case is NOT_RUN until an independent run records evidence;**",
        "a developer test is shown as developer evidence and is not a verdict.",
        "",
        f"- Cases: **{s['cases']}**; independent PASS: **{s['independent_pass']}**; NOT_RUN: **{s['not_run']}**.",
        f"- With a developer test that exercises the scenario: **{s['with_developer_evidence']}**, of which **{s['partial_or_mechanism']}** are partial or mechanism-only (noted per case).",
        f"- Need a third party (provider, register, source, partner): **{s['third_party']}**. Need an independent audit or exercise: **{s['people']}**.",
        f"- Proposed P0 (the QA lead confirms): **{s['proposed_p0']}**.",
        "",
        "| Suite | Cases | Developer evidence | Third party | People | NOT_RUN |",
        "|---|---|---|---|---|---|",
    ]
    for k in SUITES:
        v = s["by_suite"][k]
        out.append(
            f"| {k} {SUITE_NAMES[k]} | {v['cases']} | {v['with_developer_evidence']} | {v['third_party']} | {v['people']} | {v['not_run']} |"
        )
    out += [
        "",
        "## Cases with no developer evidence yet",
        "",
        "These are not failures: they are the reconciliation still to do, or scenarios that need a provider, register or independent party before any test is meaningful.",
        "",
        "| Case | Depends on | Assertion |",
        "|---|---|---|",
    ]
    out += [
        f"| {r['case_id']} | {r['dependency']} | {r['assertion']} |"
        for r in rows
        if not r["developer_evidence"].strip()
    ]
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--report", nargs="?", const=str(REPORT))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    rows = load()
    if args.json:
        print(json.dumps(summary(rows), indent=2))
        return 0
    found = problems(rows)
    if args.check:
        for p in found:
            print(p)
        stale = REPORT.exists() and REPORT.read_text(encoding="utf-8") != render(rows)
        if stale:
            print(f"{REPORT} is out of date: run with --report")
        return 1 if (found or stale) else 0
    if args.report:
        pathlib.Path(args.report).write_text(
            render(rows), encoding="utf-8", newline="\n"
        )
        print(f"wrote {args.report}")
        return 0
    print(render(rows))
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
