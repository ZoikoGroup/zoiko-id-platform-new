"""The evaluation harness for a face match and liveness engine (CR-102). Pure domain, no I/O, no model.

An engine may only be switched on with MEASURED error rates from an independent evaluation (`biometric_engine.engine_gate`). This module is the
arithmetic that evaluation needs, so the people doing it have a tool and a record the gate accepts. It does not run an engine, collect trials or
replace the independent lab: the trials must come from the evaluator, on a population like the one the product serves.

What it computes, from labelled trials:
- face match: false accept rate (impostor pairs accepted) and false reject rate (genuine pairs rejected), at the face threshold;
- liveness: attack accept rate, APCER (presentation attacks accepted, worst attack type, as ISO/IEC 30107-3 asks for per type) and bona-fide
  reject rate, BPCER, at the liveness threshold;
- each rate as the Wilson 95% UPPER bound, not the point estimate. A small sample therefore cannot look good: zero errors in 30 impostor trials is
  an upper bound near 11%, far above the 0.1% ceiling, so the draft record says the ceiling is not shown. About 3,000 clean impostor trials are
  needed to show 0.1%;
- the same rates for every cohort the evaluator labels (for example age band, skin tone, sex, device), with a disparity ratio between the best and
  the worst cohort. A cohort with too few trials is reported INSUFFICIENT_DATA, never dropped and never averaged away.

What it does not do: decide a threshold is acceptable (the security and privacy owners do), certify presentation-attack detection, or say an engine
is fair. A disparity flag is a reason to stop and look, not a verdict either way.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from zid_platform.modules.verification.domain.biometric_engine import (
    MAX_ATTACK_ACCEPT,
    MAX_BONA_FIDE_REJECT,
    MAX_FALSE_ACCEPT,
    MAX_FALSE_REJECT,
)

Kind = Literal["GENUINE", "IMPOSTOR", "BONA_FIDE", "ATTACK"]
KINDS = ("GENUINE", "IMPOSTOR", "BONA_FIDE", "ATTACK")
Z95 = 1.959963984540054  # two-sided 95%
MIN_COHORT_TRIALS = (
    100  # per kind used by a rate; below this a cohort's rate is reported INSUFFICIENT_DATA
)
DISPARITY_FLAG_RATIO = (
    2.0  # PROVISIONAL: the worst cohort's rate more than twice the best; the owners may change it
)


class EvaluationError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class Trial:
    """One scored trial. `cohort` is the evaluator's label (never derived here), `attack_type` names the presentation-attack species for ATTACK."""

    kind: Kind
    score: float
    cohort: str = "ALL"
    attack_type: str = ""


def wilson_upper(errors: int, n: int) -> float:
    """The 95% Wilson upper bound of an error rate: 1.0 when nothing was tried, so an empty or tiny sample cannot pass."""
    if n <= 0:
        return 1.0
    p = errors / n
    z2 = Z95 * Z95
    centre = p + z2 / (2 * n)
    spread = Z95 * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n))
    return min(1.0, (centre + spread) / (1 + z2 / n))


@dataclass(frozen=True, slots=True)
class Rate:
    errors: int
    trials: int

    @property
    def point(self) -> float | None:
        return None if self.trials == 0 else self.errors / self.trials

    @property
    def upper(self) -> float:
        return wilson_upper(self.errors, self.trials)


def _clean(trials: Iterable[Trial]) -> list[Trial]:
    out = []
    for t in trials:
        if t.kind not in KINDS:
            raise EvaluationError("TRIAL_KIND_UNKNOWN")
        if (
            isinstance(t.score, bool)
            or not isinstance(t.score, int | float)
            or not 0.0 <= float(t.score) <= 1.0
        ):
            raise EvaluationError(
                "TRIAL_SCORE_INVALID"
            )  # a NaN, a boolean or a score outside [0, 1] is refused whole, never skipped
        out.append(t)
    return out


def _rates(trials: list[Trial], face_min: float, live_min: float) -> dict[str, Rate]:
    g = [t for t in trials if t.kind == "GENUINE"]
    i = [t for t in trials if t.kind == "IMPOSTOR"]
    b = [t for t in trials if t.kind == "BONA_FIDE"]
    a = [t for t in trials if t.kind == "ATTACK"]
    by_type: dict[str, list[Trial]] = defaultdict(list)
    for t in a:
        by_type[t.attack_type or "UNSPECIFIED"].append(t)
    worst = Rate(0, 0)
    for rows in (
        by_type.values()
    ):  # APCER is reported for the worst attack species, as ISO/IEC 30107-3 asks
        r = Rate(sum(1 for t in rows if t.score >= live_min), len(rows))
        if worst.trials == 0 or r.upper > worst.upper:
            worst = r
    return {
        "false_accept": Rate(sum(1 for t in i if t.score >= face_min), len(i)),
        "false_reject": Rate(sum(1 for t in g if t.score < face_min), len(g)),
        "attack_accept": worst,
        "bona_fide_reject": Rate(sum(1 for t in b if t.score < live_min), len(b)),
    }


_CEILINGS = {
    "false_accept": MAX_FALSE_ACCEPT,
    "false_reject": MAX_FALSE_REJECT,
    "attack_accept": MAX_ATTACK_ACCEPT,
    "bona_fide_reject": MAX_BONA_FIDE_REJECT,
}


@dataclass(frozen=True, slots=True)
class CohortRow:
    cohort: str
    rates: Mapping[str, Rate]
    sufficient: Mapping[str, bool]


@dataclass(frozen=True, slots=True)
class Disparity:
    rate: str
    best: str
    worst: str
    ratio: (
        float | None
    )  # None when the best cohort had no errors (a ratio would be infinite or meaningless)
    flagged: bool


@dataclass(frozen=True, slots=True)
class Report:
    overall: Mapping[str, Rate]
    cohorts: tuple[CohortRow, ...]
    disparities: tuple[Disparity, ...]
    face_min: float
    liveness_min: float

    @property
    def measured(self) -> dict[str, float]:
        """The upper bounds, the numbers an engine record may honestly carry (`engine.measured` of the gate)."""
        return {k: round(v.upper, 6) for k, v in self.overall.items()}

    @property
    def problems(self) -> tuple[str, ...]:
        out = []
        for key, ceiling in _CEILINGS.items():
            r = self.overall[key]
            if r.trials == 0:
                out.append(f"NO_TRIALS_FOR_{key.upper()}")
            elif r.upper > ceiling:
                out.append(f"{key.upper()}_NOT_SHOWN_UNDER_CEILING")
        for d in self.disparities:
            if d.flagged:
                out.append(f"COHORT_DISPARITY_{d.rate.upper()}")
        return tuple(out)

    @property
    def within_ceilings(self) -> bool:
        return not any(not p.startswith("COHORT_DISPARITY") for p in self.problems)

    def draft_record(self, *, engine_id: str, engine_version: str) -> dict[str, Any]:
        """The numbers part of `config/self-run-verification/self-run.yaml`. It never sets `enabled`, the evaluation id, the DPIA reference or the
        licence statement: those are a person's, and the gate refuses an engine without them."""
        return {
            "engine": {"id": engine_id, "version": engine_version, "measured": self.measured},
            "thresholds": {"face_match_min": self.face_min, "liveness_min": self.liveness_min},
            "within_ceilings": self.within_ceilings,
            "problems": list(self.problems),
        }


def evaluate(trials: Iterable[Trial], *, face_min: float, liveness_min: float) -> Report:
    """Rates at the thresholds being proposed, overall and by cohort. Deterministic: the order of the trials never matters."""
    for name, v in (("face_min", face_min), ("liveness_min", liveness_min)):
        if isinstance(v, bool) or not isinstance(v, int | float) or not 0.5 <= float(v) <= 1.0:
            raise EvaluationError(f"THRESHOLD_INVALID_{name.upper()}")
    rows = _clean(trials)
    overall = _rates(rows, float(face_min), float(liveness_min))
    cohorts: dict[str, list[Trial]] = defaultdict(list)
    for t in rows:
        cohorts[t.cohort].append(t)
    needs = {
        "false_accept": "IMPOSTOR",
        "false_reject": "GENUINE",
        "attack_accept": "ATTACK",
        "bona_fide_reject": "BONA_FIDE",
    }
    table = []
    for name in sorted(cohorts):
        rs = _rates(cohorts[name], float(face_min), float(liveness_min))
        suff = {
            k: sum(1 for t in cohorts[name] if t.kind == needs[k]) >= MIN_COHORT_TRIALS
            for k in needs
        }
        table.append(CohortRow(name, rs, suff))
    disparities = []
    for key in needs:
        usable = [
            (c.cohort, c.rates[key])
            for c in table
            if c.sufficient[key] and c.rates[key].point is not None
        ]
        if len(usable) < 2:
            continue
        best = min(usable, key=lambda x: (x[1].point or 0.0, x[0]))
        worst = max(usable, key=lambda x: (x[1].point or 0.0, x[0]))
        bp, wp = best[1].point or 0.0, worst[1].point or 0.0
        ratio = None if bp == 0 else wp / bp
        flagged = (wp > 0 and bp == 0) or (ratio is not None and ratio > DISPARITY_FLAG_RATIO)
        disparities.append(Disparity(key, best[0], worst[0], ratio, flagged))
    return Report(overall, tuple(table), tuple(disparities), float(face_min), float(liveness_min))
