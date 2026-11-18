"""Compare our passport chip reader with an independent reader on the same documents and phones, and say whether the self-run gate would pass (E03; CR-123).

    python tools/scripts/chip_crosscheck.py RUNS_DIR [--policy FILE]

RUNS_DIR holds one JSON file per run (the format is in docs/qa/wf01-chip-crosscheck-plan.md): ours (`"reader": "ZOIKO"`) and the reference reader's (`JMRTD` or `NFCPASSPORTREADER`) for the same document, phone and
access method. The report is printed as JSON; the exit code is 0 only when the gate PASSES. With no policy recorded the gate is NOT_CONFIGURED and the exit code is 2. A bad run file stops the whole run (exit 3): a lab never
gets a partial pass from a file it could not read. Nothing is sent anywhere and no document number is in any file.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend" / "app" / "src"))

import yaml
from zid_platform.modules.evidence.domain import chip_crosscheck as cc
from zid_platform.modules.evidence.infrastructure import crosscheck_policy


def run(runs_dir: Path, policy_file: Path | None) -> tuple[dict[str, object], int]:
    runs = []
    for path in sorted(runs_dir.glob("*.json")):
        try:
            runs.append(cc.parse_run(json.loads(path.read_text(encoding="utf8"))))
        except (cc.CrosscheckError, json.JSONDecodeError) as e:
            return {"error": f"{path.name}: {e}"}, 3
    policy = (
        crosscheck_policy.parse(yaml.safe_load(policy_file.read_text(encoding="utf8")))
        if policy_file
        else crosscheck_policy.load()
    )
    comparisons, unpaired = cc.pair_runs(runs)
    verdict = cc.gate(comparisons, policy)
    report = {
        "gate": verdict.status.value,
        "reasons": list(verdict.reasons),
        "counts": dict(verdict.counts),
        "runs": len(runs),
        "unpaired": unpaired,
        "comparisons": [
            {"document": c.document_ref, "device": c.device_model, "access": c.access, "reference": c.reference, "comparable": c.comparable, "why_not": c.reason,
             "read": c.read.value, "read_differences": list(c.read_differences), "checks": {k: v.value for k, v in c.checks.items()}, "critical": list(c.critical), "ours_stricter": list(c.ours_stricter)}
            for c in comparisons
        ],
    }  # fmt: skip
    return report, {"PASS": 0, "NOT_CONFIGURED": 2}.get(verdict.status.value, 1)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("runs_dir", type=Path)
    ap.add_argument("--policy", type=Path)
    args = ap.parse_args(argv)
    report, code = run(args.runs_dir, args.policy)
    print(json.dumps(report, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
