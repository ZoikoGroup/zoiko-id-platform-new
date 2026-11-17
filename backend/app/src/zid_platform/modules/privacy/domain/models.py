"""Model and vendor lifecycle rules (ZID-PRI-004 PR-16, section 14; AT-015, AT-016, AT-017). Pure: no I/O.

What the rules enforce, each with a test:

- a model's LIVE features may never include a protected characteristic or a proxy for one ("no race, ethnicity or sex proxies in live fraud or
  eligibility scoring"). Fairness audits may use those attributes only as segregated audit attributes, never as live inputs (AT-017);
- a version is promoted only when an INDEPENDENT evaluation of exactly that version exists, with the document's measures for its kind and a
  confidence interval for each, and it is still current (AT-015). No evidence means no promotion, never a default pass;
- the version in service is pinned. A provider or deployment that reports any other version is DRIFT: the model is held and nothing new is
  decided with it until the new version has its own evaluation (AT-016);
- no measured threshold or percentage is invented here: the document says a universal figure must not be claimed, so the thresholds are the
  owner's recorded operating points and this module only checks that they are recorded.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any

KINDS: dict[str, tuple[str, ...]] = {
    "face_match": ("fmr", "fnmr", "failure_to_acquire"),
    "pad": ("apcer", "bpcer", "failure_to_acquire"),
    "fraud": ("false_positive_rate", "overturn_rate"),
    "document": ("false_reject_rate", "failure_to_acquire"),
}
# Protected characteristics and common proxies. A match is by whole word within a feature name, lower-cased.
PROTECTED = frozenset(
    {
        "race",
        "ethnicity",
        "ethnic",
        "sex",
        "gender",
        "religion",
        "disability",
        "nationality",
        "citizenship",
        "skin_tone",
        "skin_colour",
        "skin_color",
        "birthplace",
        "surname_origin",
        "age_band_demographic",
        "sexual_orientation",
        "pregnancy",
        "marital_status",
    }
)
EVALUATION_VALID = timedelta(days=365)
STATES = ("REGISTERED", "ACTIVE", "HELD", "SUSPENDED", "RETIRED")


def protected_features(features: Iterable[str]) -> list[str]:
    """Features that are, or contain as a whole word (or whole compound), a protected characteristic."""
    out = []
    for f in features:
        joined = f.lower().replace("-", "_").replace(" ", "_")
        words = set(joined.split("_"))
        if any((p in words) if "_" not in p else (p in joined) for p in PROTECTED):
            out.append(f)
    return sorted(set(out))


def evaluation_problems(kind: str, metrics: Mapping[str, Any]) -> list[str]:
    """Why an evaluation cannot back a promotion: a missing measure, a measure with no confidence interval, or no cohorts."""
    if kind not in KINDS:
        return ["MODEL_KIND_UNKNOWN"]
    problems = []
    for name in KINDS[kind]:
        m = metrics.get(name)
        if not isinstance(m, Mapping) or not all(k in m for k in ("value", "ci_low", "ci_high")):
            problems.append(f"MEASURE_MISSING_{name.upper()}")
            continue
        try:
            value, low, high = float(m["value"]), float(m["ci_low"]), float(m["ci_high"])
        except (TypeError, ValueError):
            problems.append(f"MEASURE_NOT_NUMERIC_{name.upper()}")
            continue
        if not (0.0 <= low <= value <= high <= 1.0):
            problems.append(f"MEASURE_INTERVAL_INVALID_{name.upper()}")
    return problems


def promotion_blockers(
    *,
    kind: str,
    state: str,
    evaluation: Mapping[str, Any] | None,
    now: datetime,
) -> list[str]:
    """Everything that stops this version being put into service. Empty means it may be."""
    out: list[str] = []
    if state not in ("REGISTERED", "HELD"):
        out.append("MODEL_STATE_NOT_PROMOTABLE")
    if evaluation is None:
        return [*out, "FAIRNESS_EVIDENCE_MISSING"]
    if not evaluation.get("independent"):
        out.append("EVALUATION_NOT_INDEPENDENT")
    if int(evaluation.get("cohorts", 0)) < 2:
        out.append("EVALUATION_HAS_NO_COHORT_COMPARISON")
    if not evaluation.get("accessible_alternative_confirmed"):
        out.append("ACCESSIBLE_ALTERNATIVE_NOT_CONFIRMED")
    out += evaluation_problems(kind, evaluation.get("metrics", {}))
    created = evaluation.get("created_at")
    if isinstance(created, datetime) and now - created > EVALUATION_VALID:
        out.append("EVALUATION_STALE")
    return out


def observe(pinned_version: str, seen_version: str) -> str:
    """OK when the reported version is the pinned one; DRIFT for any other, including one that looks newer."""
    return "OK" if seen_version == pinned_version else "DRIFT"


def release_blockers(models: Sequence[Mapping[str, Any]]) -> list[str]:
    """What a release gate adds for the model(s) a package names: each must be in service, not held or suspended."""
    out = []
    for m in models:
        if m["state"] != "ACTIVE":
            out.append(f"MODEL_{str(m['state']).upper()}")
    return out


def fairness_state(
    kind: str, evaluation: Mapping[str, Any] | None, *, now: datetime
) -> tuple[str, list[str]]:
    """WF-04 section 06 fairness evaluation states (B07; CR-110), from the latest evaluation of a version. Never "PASS": a result within the
    recorded operating points is PASS_WITH_LIMITS, because no evaluation shows a model is bias-free.

        NOT_RUN             no evaluation recorded
        REEVALUATION_DUE    the latest evaluation is older than its validity
        DATA_INSUFFICIENT   fewer than two cohorts compared, or a measure the kind needs is missing
        FAIL                not independent, an interval that does not hold, the accessible alternative not confirmed, or a disparity the
                            evaluator flagged (`metrics.disparity_flagged`)
        PASS_WITH_LIMITS    none of the above"""
    if evaluation is None:
        return "NOT_RUN", ["NO_EVALUATION"]
    created = evaluation.get("created_at")
    if isinstance(created, datetime) and now - created > EVALUATION_VALID:
        return "REEVALUATION_DUE", ["EVALUATION_STALE"]
    metrics = evaluation.get("metrics") or {}
    problems = evaluation_problems(kind, metrics)
    insufficient = [p for p in problems if p.startswith("MEASURE_MISSING")]
    if int(evaluation.get("cohorts", 0)) < 2:
        insufficient.append("EVALUATION_HAS_NO_COHORT_COMPARISON")
    if insufficient:
        return "DATA_INSUFFICIENT", insufficient
    failed = [p for p in problems if not p.startswith("MEASURE_MISSING")]
    if not evaluation.get("independent"):
        failed.append("EVALUATION_NOT_INDEPENDENT")
    if not evaluation.get("accessible_alternative_confirmed"):
        failed.append("ACCESSIBLE_ALTERNATIVE_NOT_CONFIRMED")
    if isinstance(metrics, Mapping) and metrics.get("disparity_flagged"):
        failed.append("COHORT_DISPARITY_FLAGGED")
    if failed:
        return "FAIL", failed
    return "PASS_WITH_LIMITS", []
