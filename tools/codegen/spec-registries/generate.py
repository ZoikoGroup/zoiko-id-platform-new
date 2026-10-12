"""Generate contract registries and traceability artifacts from extracted spec data."""
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

OUT, REPO = Path(sys.argv[1]), Path(sys.argv[2])
load = lambda n: json.load(open(OUT / f"{n}.json", encoding="utf-8"))
errors, events, reqs, tests, fams = (load(n) for n in ["errors", "events", "requirements", "tests", "families"])

# Q2 resolution (ZID-SEC-001 §33): canonical vs non-canonical sources
SUPERSEDED = {"ZID-CRYPTO-001": "ZID-CRYPTO-MI-001"}
PENDING_RECONCILIATION = {"ZID-RESILIENCE-001": "Doc 19/23", "ZID-OPS-001": "Doc 23"}
def status(src):
    if src in SUPERSEDED:
        return f"superseded (use {SUPERSEDED[src]})"
    if src in PENDING_RECONCILIATION:
        return f"supplementary, pending reconciliation into {PENDING_RECONCILIATION[src]}"
    return "canonical"
RANK = lambda src: 0 if status(src) == "canonical" else (1 if src in PENDING_RECONCILIATION else 2)

def q(s):  # YAML-safe double-quoted string
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'

def merge(items, key):
    by = defaultdict(list)
    for it in items:
        by[it[key]].append(it)
    merged, conflicts = [], []
    for k in sorted(by):
        group = sorted(by[k], key=lambda x: RANK(x["source"]))
        best = group[0]
        sources = sorted({g["source"] for g in group})
        meanings = {g["meaning"] for g in group if status(g["source"]) != status(best["source"]) or g is best} | {g["meaning"] for g in group if RANK(g["source"]) == RANK(best["source"])}
        canon_meanings = {g["meaning"] for g in group if RANK(g["source"]) == RANK(best["source"])}
        if len(canon_meanings) > 1:
            conflicts.append((k, [(g["source"], g["meaning"]) for g in group]))
        merged.append((k, best, sources))
    return merged, conflicts

# ---------------- error codes ----------------
ec, ec_conf = merge(errors, "code")
lines = ["# Canonical error / reason code registry. GENERATED from canonical specs by tools/codegen (see README).",
         "# Do not edit by hand: change the spec, then regenerate. Stable machine contracts (ZID-API-001 §3.3).", "",
         "families:  # ZID-API-001 §20"]
for f in fams:
    lines += [f"  {f['family'].rstrip('*').rstrip('_')}:", f"    use: {q(f['use'])}", f"    typical_http: {q(f['http'])}"]
lines += ["", "codes:"]
for code, best, sources in ec:
    lines += [f"  {code}:", f"    meaning: {q(best['meaning'])}", f"    source: {best['source']}",
              f"    section: {q(best['section'])}", f"    source_status: {q(status(best['source']))}"]
    if len(sources) > 1:
        lines.append(f"    also_defined_in: [{', '.join(s for s in sources if s != best['source'])}]")
(REPO / "contracts/error-codes/error-codes.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")

# ---------------- events ----------------
def lane(name):
    n = name.lower()
    if any(w in n for w in ["revoked", "compromis", "quarantin", "suspended", "risc", "caep", "emergency",
                             "key_destroyed", "disabled", "deny", "stale"]):
        return "priority"
    if n.startswith(("evidence.", "audit.")) or "receipt" in n or "checkpoint" in n:
        return "evidence"
    return "domain"
ev, ev_conf = merge(events, "event")
lines = ["# Canonical event catalogue. GENERATED from the 'Mandatory ... Events' sections of the canonical specs.",
         "# CloudEvents type rule: com.zoiko.id.<domain>.<noun>.<past-tense>.v<major> (ZID-EVENTS-001 §3).",
         "# 'lane' is derived (priority = revocation/compromise/security state; evidence = receipts/checkpoints)",
         "# and must be confirmed by the owning module when the AsyncAPI channel is written.", "", "events:"]
for name, best, sources in ev:
    lines += [f"  {name}:", f"    cloudevents_type: com.zoiko.id.{name}.v1", f"    meaning: {q(best['meaning'])}",
              f"    source: {best['source']}", f"    lane: {lane(name)}",
              f"    source_status: {q(status(best['source']))}"]
(REPO / "contracts/asyncapi/event-catalogue.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")

# ---------------- traceability ----------------
OWNER = {  # requirement prefix -> (owning units/modules, governing decisions in docs/phases/, test suites)
    "FR-TEN": ("tenant; locator", "DEC-A1, DEC-G1, DEC-G3", "tests/tenant-isolation"),
    "FR-IDN": ("principal", "DEC-A1, DEC-E1", "tests"),
    "FR-ORG": ("organization", "DEC-C2", "tests; tests/golden"),
    "FR-AUT": ("authentication, session; federation", "DEC-D3, DEC-E1", "tests/conformance; tests"),
    "FR-AUTHZ": ("decision, relationship; authority", "DEC-C1, DEC-B1, DEC-C3, DEC-C4", "tests/property; tests/golden"),
    "FR-MND": ("mandate, machine", "DEC-C2, DEC-D3, DEC-C5", "tests/concurrency; tests/property"),
    "FR-VER": ("verification", "DEC-F3", "tests"),
    "FR-VC": ("credential", "DEC-E2, DEC-E1", "tests/conformance"),
    "FR-SIG": ("signals", "DEC-B1, DEC-D2", "tests/chaos"),
    "FR-PRV": ("privacy", "DEC-F1, DEC-F3", "tests"),
    "FR-EVD": ("evidence", "DEC-F1, DEC-F2", "tests; tests/golden"),
    "FR-API": ("contracts/ (all units)", "DEC-C1, DEC-D4", "*/tests/contract"),
    "FR-OPS": ("operator_access; SRE", "DEC-F4, DEC-G5", "tests/security"),
    "FR-COM": ("privacy; Legal", "DEC-F3", "docs/traceability (evidence dossier)"),
    "NFR-PERF": ("decision, mandate (runtime plane)", "DEC-B2, DEC-B3", "tests/load"),
    "NFR-SCL": ("all units; deploy/", "DEC-B2, DEC-G1", "tests/load"),
    "NFR-REL": ("all units; deploy/", "DEC-G1", "tests/chaos"),
    "NFR-DR": ("deploy/; SRE", "DEC-G1", "tests/chaos"),
    "NFR-SEC": ("all units; .github/; deploy/policies", "DEC-F7, DEC-G4, DEC-F4", "tests/security"),
    "NFR-CRY": ("libs/zid-crypto", "DEC-F6, DEC-F5", "zid-crypto/tests"),
    "NFR-CON": ("libs/zid-db; relationship", "DEC-C1, DEC-C4, DEC-D1", "*/tests/component"),
    "NFR-DP": ("privacy, evidence", "DEC-F1", "tests"),
    "NFR-OBS": ("libs/zid-telemetry; SRE", "DEC-G5", "tests/chaos"),
    "NFR-INT": ("federation, provisioning, integration; sdks/", "DEC-A1, DEC-E1", "tests/conformance"),
    "NFR-MNT": ("all units", "DEC-A2, DEC-D1", "CI"),
    "NFR-UX": ("frontend/", "DEC-H1", "frontend/apps/*/tests"),
    "REG": ("privacy; Legal; SRE", "DEC-F3", "docs/traceability (evidence dossier)"),
}
def owner(rid):
    pre = rid.rsplit("-", 1)[0]
    return OWNER.get(pre, ("UNMAPPED", "", ""))
tdir = REPO / "docs/traceability"
with open(tdir / "requirements.csv", "w", newline="", encoding="utf-8") as fh:
    w = csv.writer(fh)
    w.writerow(["requirement_id", "priority", "requirement", "verification", "owning_units_modules",
                "governing_decisions", "test_suites", "implementation_status", "test_status", "evidence_link"])
    for r in reqs:
        o = owner(r["id"])
        w.writerow([r["id"], r["priority"], r["text"], r["verification"], o[0], o[1], o[2],
                    "NOT_STARTED", "NOT_STARTED", ""])
with open(tdir / "acceptance-tests.csv", "w", newline="", encoding="utf-8") as fh:
    w = csv.writer(fh)
    w.writerow(["test_id", "source_spec", "source_status", "name", "pass_condition", "test_status", "evidence_link"])
    seen = set()
    for t in sorted(tests, key=lambda x: (RANK(x["source"]), x["source"], x["id"])):
        key = (t["source"], t["id"])
        if key in seen:
            continue
        seen.add(key)
        w.writerow([t["id"], t["source"], status(t["source"]), t["name"], t["criterion"], "NOT_STARTED", ""])

unmapped = sorted({ r["id"].rsplit("-", 1)[0] for r in reqs if owner(r["id"])[0] == "UNMAPPED"})
by_pri = defaultdict(int)
for r in reqs:
    by_pri[r["priority"]] += 1
by_pre = defaultdict(lambda: defaultdict(int))
for r in reqs:
    by_pre[r["id"].rsplit("-", 1)[0]][r["priority"]] += 1
summary = {
    "requirements": len(reqs), "by_priority": dict(by_pri), "tests": len(seen),
    "error_codes": len(ec), "error_conflicts": ec_conf, "events": len(ev), "event_conflicts": ev_conf,
    "unmapped_prefixes": unmapped, "by_prefix": {k: dict(v) for k, v in by_pre.items()},
}
json.dump(summary, open(OUT / "summary.json", "w"), indent=1)
print(json.dumps({k: v for k, v in summary.items() if k not in ("by_prefix", "error_conflicts", "event_conflicts")}))
print("error conflicts:", len(ec_conf), " event conflicts:", len(ev_conf))
