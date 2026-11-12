"""The go-live board: one page that says, for every country and for the commercial release, what is approved, by whom, on what evidence, and what is still open.

    python tools/scripts/go_live_board.py                 # print the board
    python tools/scripts/go_live_board.py --write [PATH]  # write docs/signoff/go-live-board.md
    python tools/scripts/go_live_board.py --json          # the same facts as JSON

Sources, read only: backend/config/country-capabilities/country-capabilities.yaml (ZID-EDR-001: six go/no-go approvals per country, each with its recorded reference) and
deploy/release/ratification.json (ZID-COM-RAT-011: ratifications, gates, evidence, signatories, no-go conditions), and deploy/release/evidence-manifest.json (what was attached,
with its checksum). This tool decides nothing, fills nothing in and signs nothing: a cell is "approved" only when its owners recorded a reference, and an evidence file that
changed since it was attached is shown as CHANGED. Needs PyYAML (it is in the backend environment).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import pathlib
import re
import sys
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[2]
COUNTRIES = (
    ROOT / "backend" / "config" / "country-capabilities" / "country-capabilities.yaml"
)
REGISTER = ROOT / "deploy" / "release" / "ratification.json"
MANIFEST = ROOT / "deploy" / "release" / "evidence-manifest.json"
APPROVALS = (
    "legal_assessment",
    "supplier_coverage",
    "privacy_and_data",
    "security_and_resilience",
    "local_support",
    "signoff",
)
APPROVAL_LABEL = {
    "legal_assessment": "Legal assessment",
    "supplier_coverage": "Supplier coverage",
    "privacy_and_data": "Privacy and data",
    "security_and_resilience": "Security and resilience",
    "local_support": "Local support",
    "signoff": "Four-way sign-off",
}
REGULATED = (
    "REGISTRATION",
    "INDIVIDUAL_VERIFICATION",
    "BUSINESS_VERIFICATION",
    "CREDENTIAL_ISSUANCE",
    "ENTERPRISE_USE",
)


ACCEPTANCE = ROOT / "docs" / "traceability" / "acceptance-tests.csv"
PRIVACY_GATES = (  # ZID-PRI-004 section 23: the gate, who signs it
    ("G0", "Scope", "Product + Data owner"),
    ("G1", "Legal privacy", "Legal + Privacy / DPO"),
    ("G2", "DPIA", "Controller + DPO advice"),
    ("G3", "Biometric security", "Security / CISO"),
    ("G4", "Quality and fairness", "ML lead + Independent QA"),
    ("G5", "Supplier", "Vendor manager + Legal"),
    ("G6", "Ecosystem", "Product owners (Zoikorum, Zoiko Rooms)"),
    ("G7", "Launch", "CTO + Release manager"),
)
PRIVACY_AT_TOTAL = 50


def _counsel_fields() -> list[dict[str, str]]:
    spec = importlib.util.spec_from_file_location(
        "counsel_worksheet", ROOT / "tools" / "scripts" / "counsel_worksheet.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["counsel_worksheet"] = module
    spec.loader.exec_module(module)
    return module.fields()


def _privacy() -> dict[str, Any]:
    """ZID-PRI-004 facts that live in the repository. The per-market gate state lives in each DPIA package (the API), not here."""
    linked: dict[str, bool] = {}
    if ACCEPTANCE.exists():
        with ACCEPTANCE.open(encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                m = re.fullmatch(r"PRI4-AT-(\d{3})", row["test_id"])
                if m:
                    linked[m.group(1)] = (
                        linked.get(m.group(1), False)
                        or "MECHANISM ONLY" in row["pass_condition"]
                    )
    fields = _counsel_fields()
    return {
        "gates": [
            {"id": g, "name": n, "signer": who, "state": "per market"}
            for g, n, who in PRIVACY_GATES
        ],
        "acceptance": {
            "linked": len(linked),
            "total": PRIVACY_AT_TOTAL,
            "mechanism_only": sum(1 for v in linked.values() if v),
            "unlinked": sorted(
                f"AT-{i:03d}"
                for i in range(1, PRIVACY_AT_TOTAL + 1)
                if f"{i:03d}" not in linked
            ),
        },
        "counsel_open_fields": len(fields),
        "counsel_open_files": len({f["file"] for f in fields}),
    }


def _yaml(path: pathlib.Path) -> Any:
    try:
        import yaml
    except ImportError:  # the backend environment has it
        site = ROOT / "backend" / ".venv" / "Lib" / "site-packages"
        sys.path.insert(0, str(site))
        import yaml
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _manifest() -> dict[str, dict[str, Any]]:
    if not MANIFEST.exists():
        return {}
    return {
        e["ref"]: e
        for e in json.loads(MANIFEST.read_text(encoding="utf-8")).get("evidence", [])
    }


def _freshness(ref: str | None, manifest: dict[str, dict[str, Any]]) -> str:
    """A reference that names a file in the manifest is OK, or CHANGED if the file is not what was attached; any other reference is a recorded reference only."""
    if not ref:
        return "OPEN"
    entry = manifest.get(ref)
    if entry is None:
        return "RECORDED"
    path = ROOT / entry["path"]
    if not path.exists():
        return "MISSING"
    return (
        "ATTACHED"
        if hashlib.sha256(path.read_bytes()).hexdigest() == entry["sha256"]
        else "CHANGED"
    )


def build() -> dict[str, Any]:
    manifest = _manifest()
    reg = _yaml(COUNTRIES)
    countries = []
    for code, c in sorted(
        reg["countries"].items(), key=lambda kv: (kv[1]["wave"], kv[0])
    ):
        caps = c.get("capabilities", {})
        approvals = {}
        for cap in REGULATED:
            for a, ref in (caps.get(cap, {}).get("approvals") or {}).items():
                approvals.setdefault(a, ref)
        countries.append(
            {
                "country": code,
                "name": c["name"],
                "wave": c["wave"],
                "capabilities": {
                    k: v.get("status", "DISABLED") for k, v in caps.items()
                },
                "approvals": {
                    a: {
                        "reference": approvals.get(a),
                        "state": _freshness(approvals.get(a), manifest),
                    }
                    for a in APPROVALS
                },
                "live": any(
                    caps.get(k, {}).get("status") == "ENABLED" for k in REGULATED
                ),
            }
        )
    book = json.loads(REGISTER.read_text(encoding="utf-8"))
    return {
        "countries": countries,
        "privacy": _privacy(),
        "release": {
            "disposition": book["disposition"],
            "ratifications": [
                {
                    "id": r["id"],
                    "name": r["name"],
                    "decision": r["decision"],
                    "owner": r["owner"],
                    "effective_date": r["effective_date"],
                }
                for r in book["ratifications"]
            ],
            "gates": [
                {
                    "id": g["id"],
                    "name": g["name"],
                    "status": g["status"],
                    "reviewer": g["reviewer"],
                    "evidence": _freshness(g["evidence_ref"], manifest),
                }
                for g in book["gates"]
            ],
            "evidence": [
                {"id": e["id"], "state": _freshness(e["ref"], manifest)}
                for e in book["evidence"]
            ],
            "signatories": [
                {
                    "role": s["role"],
                    "name": s["name"],
                    "decision": s["decision"],
                    "date": s["date"],
                }
                for s in book["signatories"]
            ],
            "no_go_total": len(book["no_go"]),
        },
    }


def _mark(cell: dict[str, Any]) -> str:
    return {
        "OPEN": "open",
        "RECORDED": "recorded",
        "ATTACHED": "ATTACHED",
        "CHANGED": "CHANGED",
        "MISSING": "MISSING",
    }[cell["state"]]


def render(b: dict[str, Any]) -> str:
    out = [
        "# Go-live board",
        "",
        'Generated by `tools/scripts/go_live_board.py` from the country-capability registry and the release register. It decides nothing: a cell reads "open" until its owners record a reference.',
        "",
    ]
    out += [
        "## Countries (ZID-EDR-001, six approvals each)",
        "",
        "| Wave | Country | Live | "
        + " | ".join(APPROVAL_LABEL[a] for a in APPROVALS)
        + " |",
        "|---|---|---|" + "---|" * len(APPROVALS),
    ]
    for c in b["countries"]:
        out.append(
            f"| {c['wave']} | {c['name']} ({c['country']}) | {'yes' if c['live'] else 'no'} | "
            + " | ".join(_mark(c["approvals"][a]) for a in APPROVALS)
            + " |"
        )
    r = b["release"]
    out += [
        "",
        f"## Commercial release (ZID-COM-RAT-011): {r['disposition']}",
        "",
        "| Ratification | Decision | Owner | Effective |",
        "|---|---|---|---|",
    ]
    out += [
        f"| {x['id']} {x['name']} | {x['decision'] or 'open'} | {x['owner'] or '—'} | {x['effective_date'] or '—'} |"
        for x in r["ratifications"]
    ]
    out += ["", "| Gate | Status | Reviewer | Evidence |", "|---|---|---|---|"]
    out += [
        f"| {g['id']} {g['name']} | {g['status']} | {g['reviewer'] or '—'} | {g['evidence'].lower()} |"
        for g in r["gates"]
    ]
    out += ["", "| Signatory | Decision | Date |", "|---|---|---|"]
    out += [
        f"| {s['role']} | {s['decision'] or 'open'} | {s['date'] or '—'} |"
        for s in r["signatories"]
    ]
    pv = b["privacy"]
    ac = pv["acceptance"]
    out += [
        "",
        "## Privacy and biometric release (ZID-PRI-004, gates G0 to G7)",
        "",
        "Every gate is signed per country, product, provider and model version; the state of each is in that release's DPIA package (`GET /v2/privacy/release-gates/{market}/{product}/{version}`), not in this file. This section shows only what the repository itself holds.",
        "",
        "| Gate | Signer | State |",
        "|---|---|---|",
    ]
    out += [
        f"| {g['id']} {g['name']} | {g['signer']} | {g['state']} |" for g in pv["gates"]
    ]
    out += [
        "",
        f"- Counsel's open fields: **{pv['counsel_open_fields']}** across {pv['counsel_open_files']} files (`docs/signoff/counsel-worksheet.md`). Each is a legal statement for a market; until it is filled the platform blocks or routes to a person.",
        f"- Acceptance tests linked to a test: **{ac['linked']} of {ac['total']}**, {ac['mechanism_only']} of them mechanism-only. Not linked: {', '.join(ac['unlinked']) or 'none'}.",
        "- Owner pack: `docs/signoff/privacy-biometric-owner-pack.md`. Nothing here is a signed DPIA, a test result or a certification.",
    ]
    out += [
        "",
        f"{r['no_go_total']} no-go conditions are tracked in `deploy/release/ratification.json`; `python tools/scripts/ratification.py --report` lists each with what closes it.",
        "",
    ]
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--write", nargs="?", const=str(ROOT / "docs" / "signoff" / "go-live-board.md")
    )
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    board = build()
    if args.json:
        print(json.dumps(board, indent=2))
    elif args.write:
        pathlib.Path(args.write).write_text(render(board), encoding="utf-8")
        print(f"wrote {args.write}")
    else:
        print(render(board))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
