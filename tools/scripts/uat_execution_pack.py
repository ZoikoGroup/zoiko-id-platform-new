"""The independent execution pack (ZID-UAT-005): for each independent party, what to run, what the platform gives them, what they must bring, and how to record it.

    python tools/scripts/uat_execution_pack.py                 # print the pack
    python tools/scripts/uat_execution_pack.py --write [PATH]  # write docs/signoff/uat-independent-execution-pack.md
    python tools/scripts/uat_execution_pack.py --check         # exit 1 if the file is out of date or a case needing an outside party is in no engagement

The case lists come from `docs/traceability/uat-cases.csv`, so the pack cannot drift from the register. This is a brief for people and vendors. It runs
nothing, decides nothing, and nothing in it is a result: every case stays NOT_RUN until an independent run records signed evidence.
"""

from __future__ import annotations

import argparse
import importlib.util
import pathlib
import sys
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[2]
OUT = ROOT / "docs" / "signoff" / "uat-independent-execution-pack.md"

PROVIDER_SUITES = ("IDV", "FIN", "INT", "VC")
SOURCE_SUITES = ("DOC", "EDU", "PRO", "ORG", "RNT", "OPS")
PENTEST_CASES = (
    "GOV-01",
    "GOV-02",
    "GOV-03",
    "GOV-04",
    "GOV-05",
    "GOV-06",
    "GOV-07",
    "GOV-08",
    "IDV-04",
    "IDV-05",
    "INT-02",
    "INT-03",
    "PRI-03",
    "PRI-05",
    "PRI-06",
)

HOW = """## How any independent party records a result

1. The QA lead assigns you the TESTER role (`POST /v2/assurance/role-assignments`); nobody assigns a role to themselves.
2. You write what you did and saw into a results file (shape in the header of `tools/scripts/uat_client.py`): events as digests and references only, never a biometric, a document or a claim value.
3. Run `ZID_UAT_TOKEN=<your token> python tools/scripts/uat_client.py --base-url <cell> results.json`. It freezes the run, appends your events (running it twice records nothing twice) and signs one manifest per case.
4. The platform, not the tool, turns each manifest into a verdict. A PASS needs a complete manifest (a trace, a provider receipt or a source snapshot); a claimed PASS without one comes back INCONCLUSIVE and the tool exits non-zero.
5. If you measured a number, put it in `measurements`: it is judged against the signed assurance policy for the release unit, and a breach cannot be signed as PASS. With no signed policy the platform refuses to judge, because it supplies no numbers of its own.
6. Anyone can verify what you signed: `POST /v2/assurance/evidence:verify`, or offline against the evidence signing keys.

You must not be a commit author of the build under test, and the platform refuses a run from someone who is.
"""

ENGAGEMENTS: list[dict[str, Any]] = [
    {
        "key": "qa",
        "title": "Independent QA execution (all cases)",
        "who": "The independent QA team, led by the QA lead who signs the verdicts.",
        "provides": "Every case in the register with its assertion and the proof it needs; synthetic fixtures with second-person approval; the run client; row-level isolation so one entity's runs are invisible to another.",
        "bring": "Their own execution environment and tooling, an identity that is not in the engineering team, and the signed SLO and assurance policy before any numeric case runs.",
        "pick": lambda r: True,
        "accept": "Every applicable P0 case has a complete signed manifest with verdict PASS for the exact build and release unit (gate UAT-G0).",
    },
    {
        "key": "pentest",
        "title": "Penetration test and red team",
        "who": "An independent security test team, with a third party or independent red team behind it.",
        "provides": "The published OpenAPI contracts for every route, a tenant pair for cross-tenant (BOLA) attempts, the provider lab (12 injected faults) and the signed-webhook guards.",
        "bring": "Attack tooling and reports; PAD and injected-feed material for IDV-04 and IDV-05 (a provider or lab with an ISO/IEC 30107-3 assessment); written authorization for the target environment.",
        "pick": lambda r: r["case_id"] in PENTEST_CASES,
        "accept": "No open critical or high exploitable issue (gate UAT-G3); each listed case has a signed manifest whose trace is the attack report.",
    },
    {
        "key": "wcag",
        "title": "Accessibility audit (WCAG 2.2 AA, with assistive technology)",
        "who": "An accessibility auditor using real screen readers, keyboard-only use and zoom.",
        "provides": "The console Assurance tab and the verification session flow with its non-biometric alternatives; native, labelled controls; tests of the status wording.",
        "bring": "The audit itself, on named browsers and assistive technology, and the people who can run it.",
        "pick": lambda r: r["case_id"] in ("PRI-08", "IDV-07"),
        "accept": "A signed audit report is attached as the case's evidence; a failure on a required population is a MAJOR defect (gate UAT-G5).",
    },
    {
        "key": "bias",
        "title": "Face-match, PAD and fraud fairness study",
        "who": "An independent evaluator with lawful cohort data, under the privacy officer's approval.",
        "provides": "The model inventory and the independent-evaluation record (`/v2/privacy/models`, `/v2/privacy/model-evaluations`): measures, confidence intervals, drift hold, rollback; outcome routing that never approves or refuses on a score.",
        "bring": "The cohort data, the evaluation and its report; the lawful basis for processing the cohorts.",
        "pick": lambda r: r["case_id"] == "PRI-07",
        "accept": "An independent evaluation of exactly the version in service, with the document's measures and an interval for each, is recorded; fairness evidence missing means the version cannot be promoted.",
    },
    {
        "key": "ops",
        "title": "Load, resilience and restore exercises",
        "who": "The platform's SRE team with an independent recovery witness.",
        "provides": "`python -m zid_platform.slo_probe` for availability and latency percentiles against any deployed URL; the threshold check against the signed policy; the registry-watch route; the kill-switch and rollback routes.",
        "bring": "A deployed environment, expected traffic, two probe regions, a restore exercise with a measured RPO and RTO, and the witness's signed statement.",
        "pick": lambda r: r["case_id"] in ("OPS-04", "OPS-05", "OPS-06"),
        "accept": "Measurements are inside the signed policy's thresholds, recorded with `measurements` (gate UAT-G6).",
    },
    {
        "key": "redress",
        "title": "Redress and human-review exercise",
        "who": "The privacy officer with independent reviewers and a panel of test subjects.",
        "provides": "Appeals and human reviews of automated decisions (`/v2/decisions/{id}/appeals`, `/human-reviews`), decisions only by someone other than the requester, a notification event on every decision, and the automated-decision guard that blocks a significant, solely automated decision until counsel's rules are in force.",
        "bring": "The exercise itself: real reviewers deciding real test appeals, the effective correction, and counsel's approved rules for each market.",
        "pick": lambda r: r["case_id"] == "PRI-09",
        "accept": "The subject is informed, a person decides, the correction takes effect, and the outcome is recorded (PRI-09).",
    },
    {
        "key": "providers",
        "title": "Provider conformance and sandbox runs",
        "who": "The vendor manager with each provider's sandbox, contract and entitlement.",
        "provides": "Provider-neutral adapters, the exact-service entitlement registry, the provider lab, parity checks and controlled failover rules.",
        "bring": "Each provider's contract, sandbox credentials, certification scope and receipts. Nothing here claims a contract or a sandbox run exists.",
        "pick": lambda r: (
            r["dependency"] == "THIRD_PARTY" and r["suite"] in PROVIDER_SUITES
        ),
        "accept": "Each case has a provider receipt in its manifest; parity is explained, and an uncertified fallback never completes a regulated check as certified.",
    },
    {
        "key": "sources",
        "title": "Official registers and primary sources",
        "who": "Regional counsel, GRC and the credential operations team, with each source's owner.",
        "provides": "Pinned, offline vector loading; registry drift detection and automatic route closing; signed trusted-list verification; issuer, registry and licence rules.",
        "bring": "The official register or source (EU trusted lists, the UK digital verification register, the Right to Rent supplier register, regulators, universities, insurers, company registers), access terms that allow reading it, and dated snapshots.",
        "pick": lambda r: (
            r["dependency"] == "THIRD_PARTY" and r["suite"] in SOURCE_SUITES
        ),
        "accept": "Each case's manifest carries a dated source snapshot or receipt (gate UAT-G1 for legal and regulated status).",
    },
]


def _register() -> list[dict[str, str]]:
    spec = importlib.util.spec_from_file_location(
        "uat_register", ROOT / "tools" / "scripts" / "uat_register.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["uat_register"] = module
    spec.loader.exec_module(module)
    return module.load()


def covered_outside_parties(rows: list[dict[str, str]]) -> set[str]:
    return {
        r["case_id"]
        for e in ENGAGEMENTS
        if e["key"] != "qa"
        for r in rows
        if e["pick"](r)
    }


def uncovered(rows: list[dict[str, str]]) -> list[str]:
    """Cases that need a third party or people and appear in no engagement: nobody has been told to run them."""
    need = {r["case_id"] for r in rows if r["dependency"] != "OWN"}
    return sorted(need - covered_outside_parties(rows))


def render(rows: list[dict[str, str]]) -> str:
    out = [
        "# Independent execution pack (ZID-UAT-005)",
        "",
        "Generated by `tools/scripts/uat_execution_pack.py` from `docs/traceability/uat-cases.csv`. **This is a brief, not a result.** Every case is NOT_RUN until an independent party",
        "records signed evidence; nothing here is a pass, a certification or a sign-off, and the platform cannot supply any of the independent work below.",
        "",
        HOW,
    ]
    for e in ENGAGEMENTS:
        picked = [r for r in rows if e["pick"](r)]
        out += [
            f"## {e['title']}",
            "",
            f"- **Who:** {e['who']}",
            f"- **The platform provides:** {e['provides']}",
            f"- **They must bring:** {e['bring']}",
            f"- **Accepted when:** {e['accept']}",
            f"- **Cases:** {len(picked)}",
            "",
        ]
        if e["key"] != "qa":
            out += ["| Case | Needs | Must hold | Proof needed |", "|---|---|---|---|"]
            out += [
                f"| {r['case_id']} | {r['dependency'].lower().replace('_', ' ')} | {r['assertion']} | {r['proof_needed']} |"
                for r in picked
            ]
            out.append("")
    out += [
        "## Sign-off",
        "",
        "Release roles (QA lead, CTO, Security/GRC, Privacy, regional counsel) sign through `POST /v2/assurance/releases/{id}/decisions`, each as the role they hold and never two roles by one person.",
        "The SLO and assurance policy is proposed by the QA lead or the CTO and signed by the QA lead, the CTO and Security/GRC (three different people) through `/v2/assurance/policies`.",
        "No number in it is supplied by the platform.",
        "",
    ]
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--write", nargs="?", const=str(OUT))
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args(argv)
    rows = _register()
    text = render(rows)
    if args.check:
        gaps = uncovered(rows)
        stale = not OUT.exists() or OUT.read_text(encoding="utf-8") != text
        if gaps:
            print(
                "cases that need an outside party but are in no engagement:",
                ", ".join(gaps),
            )
        if stale:
            print(f"{OUT} is out of date: run with --write")
        return 1 if (gaps or stale) else 0
    if args.write:
        pathlib.Path(args.write).write_text(text, encoding="utf-8", newline="\n")
        print(f"wrote {args.write}")
        return 0
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
