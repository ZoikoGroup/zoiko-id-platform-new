"""Count labelled authenticity results per document type and check, and say which pairs have earned the right to be switched on (T24, T25; CR-125).

    python tools/scripts/authenticity_eval.py RESULTS_DIR [--policy FILE] [--want DOC:CHECK ...]

RESULTS_DIR holds JSON files, each a list of samples (document_type, check, genuine, attack_kind, source, device_model, verdict). The report is printed as JSON. The exit code is 0 only when every pair seen (and every
`--want` pair) PASSES; 2 when no policy is recorded; 3 on a bad file, which stops the whole run. Nothing is sent anywhere.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend" / "app" / "src"))

import yaml
from zid_platform.modules.verification.domain import authenticity_evaluation as ae
from zid_platform.modules.verification.infrastructure import authenticity_policy


def run(
    results_dir: Path, policy_file: Path | None, wanted: list[tuple[str, str]]
) -> tuple[dict[str, object], int]:
    samples: list[ae.Sample] = []
    for path in sorted(results_dir.glob("*.json")):
        try:
            samples += ae.parse_samples(json.loads(path.read_text(encoding="utf8")))
        except (ae.EvaluationError, json.JSONDecodeError) as e:
            return {"error": f"{path.name}: {e}"}, 3
    policy = (
        authenticity_policy.parse(yaml.safe_load(policy_file.read_text(encoding="utf8")))
        if policy_file
        else authenticity_policy.load()
    )
    results = ae.evaluate(samples, policy, wanted=wanted)
    report = {
        "samples": len(samples),
        "pairs": [
            {
                "document_type": r.document_type,
                "check": r.check,
                "status": r.status.value,
                "reasons": list(r.reasons),
                "counts": dict(r.counts),
            }
            for r in results
        ],
        "may_switch_on": [
            {"document_type": r.document_type, "check": r.check}
            for r in results
            if r.status is ae.Status.PASS
        ],
    }
    if policy is None:
        return report, 2
    return report, 0 if results and all(r.status is ae.Status.PASS for r in results) else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("results_dir", type=Path)
    ap.add_argument("--policy", type=Path)
    ap.add_argument("--want", action="append", default=[], metavar="DOC:CHECK")
    args = ap.parse_args(argv)
    wanted = [tuple(w.split(":", 1)) for w in args.want if ":" in w]
    report, code = run(args.results_dir, args.policy, wanted)  # type: ignore[arg-type]
    print(json.dumps(report, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
