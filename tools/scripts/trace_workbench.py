#!/usr/bin/env python3
"""Delivery and traceability workbench, as a generated report (ZID-ENG-TRC-001, Document 01; TRC-01, 02, 23; AT-03, AT-04, AT-27). No new tables, no new service.

    trace_workbench.py sheet   [--out PATH]            the section-16 response sheet, one row per requirement, from the CSVs and git; unknown stays NOT ASSESSED
    trace_workbench.py record                          freeze the digest of every evidence file now (the baseline a later change is compared with)
    trace_workbench.py stale                           list evidence whose file changed since it was recorded: a PASS on changed code is EXPIRED, never PASS
    trace_workbench.py assess  MANIFEST.json           assess one release tuple against gates G0..G4; exit 1 if any gate is blocked
    trace_workbench.py dtv                             the 34 DTV ids: the ones with no canonical definition stay MISSING_SOURCE and can never be counted done

Reads docs/traceability/*.csv and git; writes only the sheet and the baseline file. It never invents a value: a field with no source says NOT ASSESSED.
"""

from __future__ import annotations

import csv
import hashlib
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TRACE = ROOT / "docs" / "traceability"
BASELINE = TRACE / "evidence-digests.json"
NA = "NOT ASSESSED"

SHEET_FIELDS = ("req_id", "source_revision", "product", "owning_domain", "owner", "repo", "path", "commit_sha", "build_digest", "environment", "market", "tenant_cohort", "provider", "config_flag", "test_ids", "CI_run", "evidence_maturity", "decision_id", "blocker_id", "gate", "next_action", "owner_due_date")  # fmt: skip

# DTV ids the supplied register defines (Document 01, section 06). The other 19 stay MISSING_SOURCE: not renamed, not inferred, not counted as delivered.
DTV_DEFINED = {3: "Qualified electronic signing and sealing", 6: "ID-document verification", 12: "Biometric privacy and fairness", 17: "Proof of address", 18: "Liveness and biometric binding", 20: "Phone evidence", 21: "Email evidence", 22: "Device intelligence", 24: "Company ownership / UBO", 25: "Sanctions / PEP / adverse media", 26: "Open-banking evidence", 27: "Payment ownership", 32: "Transaction monitoring", 33: "Travel Rule processing", 34: "On-chain sanctions / risk"}  # fmt: skip
DTV_CONDITIONAL = {3, 26, 27, 32, 33, 34}  # off until individually commissioned, lawful, supplier-qualified, tested and signed for the market


def dtv_register() -> list[dict[str, str]]:
    out = []
    for n in range(1, 35):
        defined = n in DTV_DEFINED
        state = ("CONDITIONAL_HOLD" if n in DTV_CONDITIONAL else "DEFINED") if defined else "MISSING_SOURCE"
        out.append({"id": f"DTV-{n:02d}", "definition": DTV_DEFINED.get(n, "CANONICAL DEFINITION MISSING"), "state": state, "countable_as_done": "no"})
    return out


def _csv(name: str) -> list[dict[str, str]]:
    p = TRACE / name
    return list(csv.DictReader(p.open(encoding="utf-8", newline=""))) if p.exists() else []


def evidence_path(link: str) -> str | None:
    if not link or link.startswith("http"):
        return None
    return link.split("::", 1)[0]


def file_digest(rel: str) -> str | None:
    p = ROOT / rel
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None


# ---------------------------------------------------------------------------------------------------- the response sheet


def maturity(link: str, result: str | None) -> str:
    """E0 nothing, E1 a pointer, E2 an automated run that passed. E3 and E4 need an integrated environment and independent acceptance, which a repository cannot show."""
    if not link:
        return "E0"
    return "E2" if result == "PASSED" else "E1"


def sheet_rows(requirements: Sequence[Mapping[str, str]], acceptance: Sequence[Mapping[str, str]], results: Sequence[Mapping[str, str]]) -> list[dict[str, str]]:
    ran = {r["id"]: r for r in results}
    rows: list[dict[str, str]] = []
    for r in requirements:
        link = r.get("evidence_link", "")
        res = ran.get(r["requirement_id"], {})
        rows.append({
            **dict.fromkeys(SHEET_FIELDS, NA), "req_id": r["requirement_id"], "source_revision": "ZID-REQ-001", "owning_domain": r.get("owning_units_modules") or NA,
            "path": evidence_path(link) or NA, "commit_sha": res.get("commit") or NA, "test_ids": r.get("test_suites") or NA, "evidence_maturity": maturity(link, res.get("result")),
            "decision_id": r.get("governing_decisions") or NA, "gate": "G0" if r.get("priority") == "P0" else NA, "next_action": "attach evidence" if not link else "run and attach CI run",
        })  # fmt: skip
    for a in acceptance:
        link = a.get("evidence_link", "")
        res = ran.get(a["test_id"], {})
        rows.append({
            **dict.fromkeys(SHEET_FIELDS, NA), "req_id": a["test_id"], "source_revision": a.get("source_spec") or NA, "path": evidence_path(link) or NA, "commit_sha": res.get("commit") or NA,
            "test_ids": link.split("::", 1)[1] if "::" in link else NA, "evidence_maturity": maturity(link, res.get("result")), "next_action": "none recorded" if link else "link a test",
        })  # fmt: skip
    return rows


# ---------------------------------------------------------------------------------------------------- stale evidence (AT-04, TRC-23)


@dataclass(frozen=True)
class Staleness:
    test_id: str
    path: str
    state: str  # CURRENT, EXPIRED (file changed), UNRECORDED (never frozen), MISSING (file gone)


def staleness(acceptance: Sequence[Mapping[str, str]], baseline: Mapping[str, str], current: Mapping[str, str | None]) -> list[Staleness]:
    """A test result belongs to the exact file it ran. If the file digest is not the recorded one, the PASS is EXPIRED and cannot close a gate."""
    out = []
    for a in acceptance:
        p = evidence_path(a.get("evidence_link", ""))
        if not p:
            continue
        now = current.get(p)
        if now is None:
            out.append(Staleness(a["test_id"], p, "MISSING"))
        elif p not in baseline:
            out.append(Staleness(a["test_id"], p, "UNRECORDED"))
        else:
            out.append(Staleness(a["test_id"], p, "CURRENT" if baseline[p] == now else "EXPIRED"))
    return out


def valid_pass(s: Staleness) -> bool:
    return s.state == "CURRENT"


# ---------------------------------------------------------------------------------------------------- the release tuple and gates (AT-27)

GATES = ("G0", "G1", "G2", "G3", "G4")
G3_SIGNERS = ("release_manager", "qa", "security", "privacy_legal")


@dataclass
class Manifest:
    release_id: str
    market: str | None = None
    tenant_class: str | None = None
    provider: str | None = None
    method: str | None = None
    build_digest: str | None = None
    approved_markets: Sequence[str] = ()
    dtv_missing: int = 19
    p0_without_evidence: int = 0
    cross_tenant_failures: int = 0
    unqualified_sources: int = 0
    partner_e2e_signed_webhook: bool = False
    unsupported_public_claims: int = 0
    open_material_incidents: int = 0
    evidence_expired: int = 0
    signatures: Mapping[str, str] = field(default_factory=dict)  # role -> person
    implementer: str | None = None
    rollback_rehearsed: bool = False


@dataclass(frozen=True)
class GateResult:
    gate: str
    passed: bool
    blockers: tuple[str, ...]


def assess(m: Manifest) -> list[GateResult]:
    """Each gate lists what blocks it. A gate passes only with no blocker, and a later gate cannot pass while an earlier one is blocked. Nothing is inferred from another market."""
    out: dict[str, list[str]] = {g: [] for g in GATES}
    for name, v in (("market", m.market), ("tenant_class", m.tenant_class), ("provider", m.provider), ("method", m.method)):
        if not v:
            out["G0"].append(f"{name} is not set: NOT ASSESSED, never assumed global")
    if m.market and m.market not in m.approved_markets:
        out["G0"].append(f"market {m.market} has no approved scope; another market approval does not carry over")
    if m.dtv_missing:
        out["G0"].append(f"{m.dtv_missing} DTV requirement(s) have no canonical definition")
    if m.p0_without_evidence:
        out["G0"].append(f"{m.p0_without_evidence} P0 requirement(s) have no evidence")
    if not m.build_digest:
        out["G1"].append("no build digest: not deployed as far as the register can show")
    if m.cross_tenant_failures:
        out["G1"].append("a cross-tenant bypass is open")
    if m.unqualified_sources:
        out["G1"].append("an unqualified trust source or provider is in the route")
    if m.evidence_expired:
        out["G1"].append(f"{m.evidence_expired} test result(s) are expired because the code changed")
    if not m.partner_e2e_signed_webhook:
        out["G2"].append("no independent partner end-to-end run with a signed final webhook")
    for role in G3_SIGNERS:
        who = m.signatures.get(role)
        if not who:
            out["G3"].append(f"{role} has not signed")
        elif who == m.implementer:
            out["G3"].append(f"{role} is the implementer: no self-approval")
    signers = [v for v in m.signatures.values() if v]
    if len(set(signers)) < len(signers):
        out["G3"].append("one person signed in more than one role")
    if m.unsupported_public_claims:
        out["G3"].append(f"{m.unsupported_public_claims} unsupported public claim(s)")
    if not m.rollback_rehearsed:
        out["G3"].append("rollback has not been rehearsed")
    if m.open_material_incidents:
        out["G4"].append("a material incident is unresolved")
    if m.evidence_expired:
        out["G4"].append("evidence has expired")
    res = []
    blocked_before = False
    for g in GATES:
        b = list(out[g])
        if blocked_before:
            b.append("an earlier gate is blocked")
        res.append(GateResult(g, not b, tuple(b)))
        blocked_before = blocked_before or bool(out[g])
    return res


def production_eligible(results: Sequence[GateResult]) -> bool:
    return all(r.passed for r in results)


def manifest_from_json(d: Mapping[str, object]) -> Manifest:
    raw = d.get("scope")
    scope: Mapping[str, object] = raw if isinstance(raw, dict) else {}
    known = {k: d[k] for k in Manifest.__dataclass_fields__ if k in d}
    merged = {"release_id": str(d.get("release_id", "UNNAMED")), **{k: scope.get(k) for k in ("market", "tenant_class", "provider", "method")}, **known}
    return Manifest(**merged)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------------------------------- commands


def main(argv: list[str] | None = None) -> int:
    a = list(argv if argv is not None else sys.argv[1:])
    cmd = a[0] if a else "sheet"
    if cmd == "dtv":
        for r in dtv_register():
            print(f"{r['id']}  {r['state']:17} {r['definition']}")
        return 0
    if cmd == "assess":
        m = manifest_from_json(json.loads(Path(a[1]).read_text(encoding="utf-8")))
        results = assess(m)
        for r in results:
            print(f"{r.gate}: {'PASS' if r.passed else 'BLOCKED'}")
            for b in r.blockers:
                print(f"   - {b}")
        print(f"production_eligible: {production_eligible(results)}")
        return 0 if production_eligible(results) else 1
    acc, req, res = _csv("acceptance-tests.csv"), _csv("requirements.csv"), _csv("evidence-results.csv")
    paths = {p for p in (evidence_path(r.get("evidence_link", "")) for r in acc) if p}
    if cmd == "sheet":
        rows = sheet_rows(req, acc, res)
        out = open(a[a.index("--out") + 1], "w", encoding="utf-8", newline="") if "--out" in a else sys.stdout  # noqa: SIM115
        w = csv.DictWriter(out, fieldnames=SHEET_FIELDS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
        if out is not sys.stdout:
            out.close()
        print(f"{len(rows)} rows; {sum(1 for r in rows if r['evidence_maturity'] == 'E0')} with no evidence (E0)", file=sys.stderr)
        return 0
    if cmd == "record":
        cur = {p: file_digest(p) for p in sorted(paths)}
        BASELINE.write_text(json.dumps({k: v for k, v in cur.items() if v}, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        print(f"recorded {len([v for v in cur.values() if v])} evidence file digests")
        return 0
    if cmd == "stale":
        base = json.loads(BASELINE.read_text(encoding="utf-8")) if BASELINE.exists() else {}
        st = staleness(acc, base, {p: file_digest(p) for p in paths})
        bad = [s for s in st if not valid_pass(s)]
        for s in bad[:200]:
            print(f"{s.state:10} {s.test_id} {s.path}")
        print(f"{len(st) - len(bad)} current, {len(bad)} not current", file=sys.stderr)
        return 1 if any(s.state in ("EXPIRED", "MISSING") for s in bad) else 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
