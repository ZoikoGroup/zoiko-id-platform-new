"""Turn an independent evaluator's raw biometric trial file into the measures the model gate reads (Document 04 PR-15, PR-16).

    python tools/scripts/trial_report.py trials.json --kind pad
    python tools/scripts/trial_report.py trials.json --kind face_match --confidence 0.95 > evaluation.json

`trials.json` is a JSON list of trial records exactly as the evaluator logged them (shapes in `privacy/domain/trial_metrics.py`). The output holds
`metrics` (each measure as value and exact binomial interval, with the counts behind it), `cohorts` (how many cohorts the trials name) and, when
every trial names a cohort, `cohort_differences` for a fairness review. Put `metrics` and `cohorts` into `POST /v2/privacy/model-evaluations`
together with the evaluator's reference and `independent: true` ONLY if the evaluation really was independent.

This tool decides nothing and defines no threshold. It cannot make an evaluation independent, and it does not run an engine: the trials have to come
from a real engine on real, consented cohorts, run by someone who is not the vendor. Standard library only.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend" / "app" / "src"))

from zid_platform.modules.privacy.domain import trial_metrics as tm


def report(
    kind: str, trials: list[dict[str, Any]], confidence: float
) -> dict[str, Any]:
    if kind == "pad":
        metrics = tm.pad_measures(trials, confidence)
    elif kind == "face_match":
        metrics = tm.face_match_measures(trials, confidence)
    else:
        raise tm.TrialError("KIND_NOT_SUPPORTED", kind)
    cohorts = {t.get("cohort") for t in trials if t.get("cohort")}
    out: dict[str, Any] = {"kind": kind, "metrics": metrics, "cohorts": len(cohorts)}
    if cohorts and all(t.get("cohort") for t in trials):
        if kind == "pad":
            out["cohort_differences"] = tm.pad_cohort_differences(trials, confidence)
        else:
            out["cohort_differences"] = {
                "fnmr": tm.cohort_differences(
                    trials,
                    lambda t: (
                        (not t["accepted"]) if t["acquired"] and t["genuine"] else None
                    ),
                    confidence,
                ),
                "fmr": tm.cohort_differences(
                    trials,
                    lambda t: (
                        t["accepted"] if t["acquired"] and not t["genuine"] else None
                    ),
                    confidence,
                ),
            }
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("trials", help="JSON file: a list of trial records")
    ap.add_argument("--kind", required=True, choices=("pad", "face_match"))
    ap.add_argument("--confidence", type=float, default=0.95)
    args = ap.parse_args(argv)
    try:
        trials = json.loads(pathlib.Path(args.trials).read_text(encoding="utf-8"))
        if not isinstance(trials, list) or not all(isinstance(t, dict) for t in trials):
            raise tm.TrialError("TRIALS_NOT_A_LIST_OF_RECORDS")
        print(
            json.dumps(
                report(args.kind, trials, args.confidence), indent=2, sort_keys=True
            )
        )
    except (tm.TrialError, OSError, json.JSONDecodeError, KeyError) as e:
        print(f"refused: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
