"""Commercial ratification and go-live register (ZID-COM-RAT-011). Standard library only.

    python3 tools/scripts/ratification.py --check                    # the register is well formed and says nothing it cannot back
    python3 tools/scripts/ratification.py --report [PATH]            # write the checklist for the signatories
    python3 tools/scripts/ratification.py --require-go               # exit 1 unless the disposition is GO and every no-go is closed

The register (`deploy/release/ratification.json`) is the owners' instrument. Engineering built this checker; it never decides a gate,
fills a blank or signs. What it does, fail-closed:

  * the default is NO-GO: a blank register is valid and is NO-GO;
  * GO needs every ratification decided, every gate PASS with a named reviewer and an evidence reference that resolves, every evidence
    item E01 to E07 referenced, the release targeting fields filled, all seven signatories signed, the Founder and the CFO two different
    people, and every no-go condition closed;
  * CONDITIONAL GO can never override an open P0 no-go;
  * an Amend or a Defer needs a decision record, a revised policy version, an owner and an effective date;
  * a no-go condition closes only when its machine checks hold (the ratification is Approve, the gate is PASS, the acceptance rows are
    VERIFIED, the CI jobs exist and are required by the `checks` gate, the finance policy lock is sandbox-only);
  * a policy flag whose closure is unratified must still be in a disabled value, and a money flag must still be at its shipped value.

Passing `--check` means the register is consistent, never that the product may go live.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
REGISTER = ROOT / "deploy" / "release" / "ratification.json"
ACCEPTANCE = ROOT / "docs" / "traceability" / "acceptance-tests.csv"
CI = ROOT / ".github" / "workflows" / "ci.yml"
FINANCE_SERVICE = ROOT / "backend/app/src/zid_platform/modules/commercial/application/finance_service.py"

DISPOSITIONS = ("NO-GO", "CONDITIONAL_GO", "GO")
DECISIONS = ("Approve", "Amend", "Defer")
GATE_STATUS = ("PENDING", "PASS", "FAIL")
VERIFIED = ("VERIFIED", "ACCEPTED")
RELEASE_FIELDS = ("release_id", "policy_version", "approved_countries", "approved_products", "approved_issuers", "environment", "date")


def load(path: Path = REGISTER) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _blank(v: Any) -> bool:
    return v is None or (isinstance(v, str) and not v.strip()) or v == []


def evidence_resolves(ref: str | None, root: Path = ROOT) -> bool:
    """A link is `https://...`, or a repository path with an optional `::test_function`, and the path must exist."""
    if _blank(ref):
        return False
    assert ref is not None
    if ref.startswith("https://"):
        return True
    path, _, func = ref.partition("::")
    f = root / path
    if not f.is_file():
        return False
    return not func or re.search(rf"\bdef {re.escape(func)}\b", f.read_text(encoding="utf-8")) is not None


# ----------------------------------------------------------------------------------------------------------- machine signals


def acceptance_status(path: Path = ACCEPTANCE) -> dict[str, str]:
    with path.open(encoding="utf-8", newline="") as fh:
        return {r["test_id"]: r["test_status"] for r in csv.DictReader(fh)}


def required_ci_jobs(ci_text: str) -> tuple[set[str], set[str]]:
    """(job names defined, job names the `checks` gate needs), read from ci.yml without a YAML library."""
    defined = set(re.findall(r"^  ([a-z][a-z0-9-]*):\s*$", ci_text, re.MULTILINE))
    m = re.search(r"^  checks:\s*$.*?^    needs:\s*\n((?:      - [a-z0-9-]+\s*\n)+)", ci_text, re.MULTILINE | re.DOTALL)
    needed = set(re.findall(r"- ([a-z0-9-]+)", m.group(1))) if m else set()
    return defined, needed


def finance_lock_is_sandbox_only(text: str) -> bool:
    """A deployed environment never acts on an unsigned finance policy: the only environments that skip the lock are local and test."""
    m = re.search(r"^SANDBOX\s*=\s*frozenset\(\{([^}]*)\}\)", text, re.MULTILINE)
    return m is not None and {s.strip().strip("\"'") for s in m.group(1).split(",") if s.strip()} == {"local", "test"}


def flag_values(text: str, key: str) -> list[str]:
    """Every uncommented `key: value` line, value without a trailing comment or quotes."""
    out = []
    for m in re.finditer(rf"^[ \t]*{re.escape(key)}:[ \t]*([^\s#][^#\n]*)", text, re.MULTILINE):
        out.append(m.group(1).strip().strip("\"'"))
    return out


# ---------------------------------------------------------------------------------------------------------------------- rules


def no_go_state(item: dict[str, Any], reg: dict[str, Any], *, rows: dict[str, str], ci_text: str, finance_text: str) -> tuple[str, list[str]]:
    """`CLOSED` only when every named check holds; otherwise `OPEN` with the reasons. Nothing here can close a condition by itself."""
    c = item.get("closes_when", {})
    why: list[str] = []
    decided = {r["id"]: r.get("decision") for r in reg["ratifications"]}
    for rid in c.get("ratified", []):
        if decided.get(rid) != "Approve":
            why.append(f"{rid} is not Approved")
    gates = {g["id"]: g.get("status") for g in reg["gates"]}
    for gid in c.get("gates_pass", []):
        if gates.get(gid) != "PASS":
            why.append(f"gate {gid} is not PASS")
    for tid in c.get("rows_verified", []):
        if rows.get(tid) not in VERIFIED:
            why.append(f"acceptance row {tid} is {rows.get(tid, 'missing')}, not VERIFIED")
    if c.get("ci_jobs"):
        defined, needed = required_ci_jobs(ci_text)
        for job in c["ci_jobs"]:
            if job not in defined:
                why.append(f"CI job {job} does not exist")
            elif job not in needed:
                why.append(f"CI job {job} is not required by the checks gate")
    if c.get("finance_policy_lock") and not finance_lock_is_sandbox_only(finance_text):
        why.append("the finance policy lock is no longer sandbox-only")
    return ("OPEN", why) if why else ("CLOSED", [])


def validate(reg: dict[str, Any], *, root: Path = ROOT) -> list[str]:
    """Everything wrong with the register as written. An empty list means consistent, not approved."""
    p: list[str] = []
    disp = reg.get("disposition")
    if disp not in DISPOSITIONS:
        p.append(f"disposition {disp!r} is not one of {DISPOSITIONS}")
    ids = [r["id"] for r in reg.get("ratifications", [])]
    if ids != [f"CL-00{i}" for i in range(1, 7)]:
        p.append(f"the ratification register must list CL-001 to CL-006 in order, found {ids}")
    for r in reg.get("ratifications", []):
        d = r.get("decision")
        if d is not None and d not in DECISIONS:
            p.append(f"{r['id']}: decision {d!r} is not Approve, Amend or Defer")
        if d in ("Amend", "Defer"):
            for f in ("decision_record", "policy_version", "owner", "effective_date"):
                if _blank(r.get(f)):
                    p.append(f"{r['id']}: an {d} needs {f}")
        if d == "Approve" and _blank(r.get("owner")):
            p.append(f"{r['id']}: an Approve needs a named owner")
    gids = [g["id"] for g in reg.get("gates", [])]
    if gids != [f"G{i}" for i in range(7)]:
        p.append(f"the gates must be G0 to G6 in order, found {gids}")
    for g in reg.get("gates", []):
        if g.get("status") not in GATE_STATUS:
            p.append(f"{g['id']}: status {g.get('status')!r} is not one of {GATE_STATUS}")
        if g.get("status") == "PASS":
            if _blank(g.get("reviewer")):
                p.append(f"{g['id']}: PASS needs a named reviewer")
            if not evidence_resolves(g.get("evidence_ref"), root):
                p.append(f"{g['id']}: PASS needs an evidence reference that resolves")
    for e in reg.get("evidence", []):
        if not _blank(e.get("ref")) and not evidence_resolves(e["ref"], root):
            p.append(f"{e['id']}: evidence reference {e['ref']!r} does not resolve")
    if [e["id"] for e in reg.get("evidence", [])] != [f"E0{i}" for i in range(1, 8)]:
        p.append("the evidence register must list E01 to E07 in order")
    roles = [s["role"] for s in reg.get("signatories", [])]
    if roles != ["Founder", "CFO", "CCO", "CTO", "Security and Privacy", "Finance Controller", "Legal"]:
        p.append(f"the seven signatories must be Founder, CFO, CCO, CTO, Security and Privacy, Finance Controller and Legal, found {roles}")
    sig = {s["role"]: s for s in reg.get("signatories", [])}
    f, c = sig.get("Founder", {}), sig.get("CFO", {})
    if not _blank(f.get("name")) and not _blank(c.get("name")) and f["name"].strip().lower() == c["name"].strip().lower():
        p.append("the Founder and the CFO must be two different people")
    for s in reg.get("signatories", []):
        if s.get("decision") not in (None, "GO", "CONDITIONAL_GO", "NO-GO"):
            p.append(f"{s['role']}: decision {s.get('decision')!r} is not GO, CONDITIONAL_GO or NO-GO")
    return p + _flag_problems(reg, root) + _disposition_problems(reg, root)


def _flag_problems(reg: dict[str, Any], root: Path) -> list[str]:
    p: list[str] = []
    decided = {r["id"]: r.get("decision") for r in reg.get("ratifications", [])}
    for fl in reg.get("flags", []):
        path = root / fl["file"]
        if not path.is_file():
            p.append(f"{fl['id']}: {fl['file']} does not exist")
            continue
        values = flag_values(path.read_text(encoding="utf-8"), fl["key"])
        if not values:
            p.append(f"{fl['id']}: {fl['key']} is not set in {fl['file']}")
            continue
        if any(v != fl["shipped"] for v in values):
            p.append(f"{fl['id']}: {fl['file']} says {values}, the register says it ships as {fl['shipped']}; change the register with the file")
        gate = fl.get("off_until_ratified")
        if gate and decided.get(gate) != "Approve" and any(v not in fl["off_values"] for v in values):
            p.append(f"{fl['id']}: {gate} is not Approved, so {fl['key']} must stay in {fl['off_values']}, not {values}")
    for fl in reg.get("money_flags", []):
        path = root / fl["file"]
        values = flag_values(path.read_text(encoding="utf-8"), fl["key"]) if path.is_file() else []
        if not values:
            p.append(f"{fl['id']}: {fl['key']} is not set in {fl['file']}")
        elif any(v != fl["shipped"] for v in values):
            p.append(f"{fl['id']}: a money flag is not at its shipped value {fl['shipped']}: {values}")
    return p


def _disposition_problems(reg: dict[str, Any], root: Path) -> list[str]:
    disp = reg.get("disposition")
    if disp == "NO-GO":
        return []
    p: list[str] = []
    state, reasons = verdict(reg, root=root)
    open_p0 = [n for n in reg.get("no_go", []) if n.get("p0") and _no_go(n, reg, root)[0] == "OPEN"]
    if disp == "CONDITIONAL_GO" and open_p0:
        p.append("a CONDITIONAL GO cannot override an unresolved P0 no-go: " + ", ".join(n["id"] for n in open_p0))
    if disp == "GO" and state != "GO":
        p.append("the disposition says GO but the register does not support it: " + "; ".join(reasons))
    return p


def _no_go(item: dict[str, Any], reg: dict[str, Any], root: Path) -> tuple[str, list[str]]:
    ci = (root / ".github/workflows/ci.yml")
    fs = root / "backend/app/src/zid_platform/modules/commercial/application/finance_service.py"
    acc = root / "docs/traceability/acceptance-tests.csv"
    return no_go_state(
        item,
        reg,
        rows=acceptance_status(acc) if acc.is_file() else {},
        ci_text=ci.read_text(encoding="utf-8") if ci.is_file() else "",
        finance_text=fs.read_text(encoding="utf-8") if fs.is_file() else "",
    )


def verdict(reg: dict[str, Any], *, root: Path = ROOT) -> tuple[str, list[str]]:
    """`GO` only when everything the document asks for is present and every no-go is closed; otherwise `NO-GO` and why. The recorded
    disposition is an input the owners set; it never overrides this."""
    why: list[str] = []
    for r in reg["ratifications"]:
        if r.get("decision") is None:
            why.append(f"{r['id']} has no decision")
        elif r["decision"] != "Approve":
            why.append(f"{r['id']} is {r['decision']}, not Approve")
    for g in reg["gates"]:
        if g.get("status") != "PASS":
            why.append(f"gate {g['id']} is {g.get('status')}")
    for e in reg["evidence"]:
        if _blank(e.get("ref")):
            why.append(f"evidence {e['id']} is not referenced")
    for k in RELEASE_FIELDS:
        if _blank(reg["release"].get(k)):
            why.append(f"release field {k} is blank")
    for s in reg["signatories"]:
        if s.get("decision") != "GO" or _blank(s.get("name")) or _blank(s.get("date")):
            why.append(f"{s['role']} has not signed GO")
    for n in reg["no_go"]:
        st, reasons = _no_go(n, reg, root)
        if st == "OPEN":
            why.append(f"{n['id']} is open ({'; '.join(reasons)})")
    return ("NO-GO", why) if why else ("GO", [])


# ---------------------------------------------------------------------------------------------------------------------- report


def report(reg: dict[str, Any], *, root: Path = ROOT) -> str:
    state, why = verdict(reg, root=root)
    rows = acceptance_status(root / "docs/traceability/acceptance-tests.csv")
    counts: dict[str, int] = {}
    for v in rows.values():
        counts[v] = counts.get(v, 0) + 1
    L = [
        "# Release ratification and go-live checklist (ZID-COM-RAT-011)",
        "",
        f"Generated {date.today().isoformat()} by `tools/scripts/ratification.py` from `deploy/release/ratification.json`. This is a checklist for the signatories, not a decision: "
        "only the owners fill in the register. The default is NO-GO, and a recorded GO is refused by the checker unless everything below supports it.",
        "",
        f"- Recorded disposition: **{reg['disposition']}**",
        f"- What the register supports today: **{state}** ({len(why)} open item{'s' if len(why) != 1 else ''})",
        f"- Acceptance rows: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())),
        "",
        "## Ratification register",
        "",
        "| ID | Closure | Decision | Decision record | Policy version | Owner | Effective |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in reg["ratifications"]:
        L.append(f"| {r['id']} | {r['name']} | {r['decision'] or '—'} | {r['decision_record'] or '—'} | {r['policy_version'] or '—'} | {r['owner'] or '—'} | {r['effective_date'] or '—'} |")
    L += ["", "## Gates", "", "| Gate | Name | Status | Reviewer | Evidence |", "|---|---|---|---|---|"]
    for g in reg["gates"]:
        L.append(f"| {g['id']} | {g['name']} | {g['status']} | {g['reviewer'] or '—'} | {g['evidence_ref'] or '—'} |")
    L += ["", "## No-go conditions", "", "| ID | Condition | State | Why it is still open |", "|---|---|---|---|"]
    for n in reg["no_go"]:
        st, reasons = _no_go(n, reg, root)
        L.append(f"| {n['id']} | {n['text']} | {st} | {'; '.join(reasons) or '—'} |")
    L += ["", "## Policy flags", "", "| Flag | File | Key | Shipped | Disabled until |", "|---|---|---|---|---|"]
    for f in reg["flags"]:
        L.append(f"| {f['id']} | `{f['file']}` | `{f['key']}` | {f['shipped']} | {f.get('off_until_ratified') or '—'} |")
    for f in reg["money_flags"]:
        L.append(f"| {f['id']} | `{f['file']}` | `{f['key']}` | {f['shipped']} | owners' switch |")
    L += ["", "## Release targeting", "", "| Field | Value |", "|---|---|"]
    for k in RELEASE_FIELDS:
        v = reg["release"].get(k)
        L.append(f"| {k} | {', '.join(v) if isinstance(v, list) and v else (v or '—')} |")
    L += ["", "## Evidence register", "", "| ID | Reference |", "|---|---|"]
    L += [f"| {e['id']} | {e['ref'] or '—'} |" for e in reg["evidence"]]
    L += ["", "## Signatories", "", "| Role | Name | Date | Decision |", "|---|---|---|---|"]
    L += [f"| {s['role']} | {s['name'] or ''} | {s['date'] or ''} | {s['decision'] or ''} |" for s in reg["signatories"]]
    L += ["", "## Open items", ""]
    L += [f"- {w}" for w in why] or ["None."]
    L.append("")
    return "\n".join(L)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--register", type=Path, default=REGISTER)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true")
    g.add_argument("--report", nargs="?", const="docs/signoff/release-ratification.md")
    g.add_argument("--require-go", action="store_true")
    args = ap.parse_args(argv)
    reg = load(args.register)
    if args.check:
        problems = validate(reg)
        for x in problems:
            print(f"ratification: {x}", file=sys.stderr)
        state, why = verdict(reg)
        print(f"ratification register: {'consistent' if not problems else 'INCONSISTENT'}; supports {state} ({len(why)} open)")
        return 1 if problems else 0
    if args.report is not None:
        out = ROOT / args.report
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(report(reg), encoding="utf-8")
        print(f"wrote {out.relative_to(ROOT)}")
        return 0
    problems = validate(reg)
    state, why = verdict(reg)
    if problems or state != "GO" or reg.get("disposition") != "GO":
        for x in problems:
            print(f"ratification: {x}", file=sys.stderr)
        for x in why:
            print(f"NO-GO: {x}", file=sys.stderr)
        if reg.get("disposition") != "GO":
            print(f"NO-GO: the recorded disposition is {reg.get('disposition')}", file=sys.stderr)
        return 1
    print("GO: the register supports the recorded disposition")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
