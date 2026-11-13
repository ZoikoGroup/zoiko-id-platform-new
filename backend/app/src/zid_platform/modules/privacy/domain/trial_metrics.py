"""Turn an independent evaluator's raw trial results into the measures the model gate asks for (Document 04 PR-15, PR-16; AT-015, AT-016). Pure.

The model-governance gate (`models.py`) will not promote a biometric model without `{value, ci_low, ci_high}` for each measure of its kind. This module
computes exactly that from raw trials, so a figure can be checked and nobody has to type an interval by hand. It decides NOTHING:

- no threshold is defined here (what rate is acceptable is the DPO's and the evaluator's, in a signed policy); the output is measures and intervals;
- the evidence still has to come from an independent evaluator running a real engine on real cohorts: this code cannot make one;
- intervals are exact binomial (Clopper-Pearson, 95% unless told otherwise), which is conservative for the small samples that biometric
  evaluations often have; a sample size is always reported beside a rate so a reader can see how little a small sample proves;
- a trial where nothing was acquired is a failure to acquire, reported on its own and never folded into an error rate (ISO/IEC 30107-3 reports them
  separately), and APCER is reported per presentation-attack species with the WORST species as the headline, never averaged away.

Trial shapes (all plain dictionaries):
  pad:        {"attack": bool, "species": str | None, "accepted": bool, "acquired": bool, "cohort": str | None}
              an attack that was accepted is an error (APCER); a bona fide presentation that was rejected is an error (BPCER)
  face_match: {"genuine": bool, "accepted": bool, "acquired": bool, "cohort": str | None}
              an impostor comparison accepted is an error (FMR); a genuine comparison rejected is an error (FNMR)
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from typing import Any


class TrialError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


def _log_pmf(k: int, n: int, p: float) -> float:
    if p <= 0.0:
        return 0.0 if k == 0 else -math.inf
    if p >= 1.0:
        return 0.0 if k == n else -math.inf
    return (
        math.lgamma(n + 1)
        - math.lgamma(k + 1)
        - math.lgamma(n - k + 1)
        + k * math.log(p)
        + (n - k) * math.log1p(-p)
    )


def _cdf(k: int, n: int, p: float) -> float:
    """P(X <= k) for X ~ Binomial(n, p)."""
    if k >= n:
        return 1.0
    if k < 0:
        return 0.0
    return min(1.0, sum(math.exp(_log_pmf(i, n, p)) for i in range(k + 1)))


def _bisect(f: Any, lo: float, hi: float) -> float:
    for _ in range(80):
        mid = (lo + hi) / 2
        if f(mid):
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


def clopper_pearson(events: int, trials: int, confidence: float = 0.95) -> tuple[float, float]:
    """The exact two-sided binomial interval for `events` out of `trials`."""
    if trials <= 0 or events < 0 or events > trials:
        raise TrialError("TRIALS_INVALID", f"{events}/{trials}")
    if not 0.5 < confidence < 1.0:
        raise TrialError("CONFIDENCE_INVALID", str(confidence))
    tail = (1.0 - confidence) / 2.0
    low = (
        0.0
        if events == 0
        # the smallest p for which seeing `events` or more has probability at least `tail`
        else _bisect(lambda p: 1.0 - _cdf(events - 1, trials, p) >= tail, 0.0, 1.0)
    )
    high = (
        1.0
        if events == trials
        # the largest p for which seeing `events` or fewer has probability at least `tail`
        else _bisect(lambda p: _cdf(events, trials, p) <= tail, 0.0, 1.0)
    )
    return low, high


def measure(events: int, trials: int, confidence: float = 0.95) -> dict[str, Any]:
    """One rate in the shape the model gate reads, with the counts it came from."""
    low, high = clopper_pearson(events, trials, confidence)
    return {
        "value": events / trials,
        "ci_low": low,
        "ci_high": high,
        "events": events,
        "trials": trials,
        "confidence": confidence,
    }


def _flag(trial: Mapping[str, Any], key: str) -> bool:
    value = trial.get(key)
    if not isinstance(value, bool):
        raise TrialError("TRIAL_FIELD_NOT_BOOLEAN", key)
    return value


def _failure_to_acquire(trials: Sequence[Mapping[str, Any]], confidence: float) -> dict[str, Any]:
    failed = sum(1 for t in trials if not _flag(t, "acquired"))
    return measure(failed, len(trials), confidence)


def pad_measures(trials: Iterable[Mapping[str, Any]], confidence: float = 0.95) -> dict[str, Any]:
    """APCER (worst species is the headline, every species listed), BPCER and failure to acquire, from raw presentation-attack-detection trials.

    Errors out rather than guessing when there is nothing to measure: an evaluation with no attack presentations or no bona fide ones is not one."""
    rows = list(trials)
    if not rows:
        raise TrialError("NO_TRIALS")
    acquired = [t for t in rows if _flag(t, "acquired")]
    attacks = [t for t in acquired if _flag(t, "attack")]
    bona_fide = [t for t in acquired if not _flag(t, "attack")]
    if not attacks:
        raise TrialError("NO_ATTACK_PRESENTATIONS")
    if not bona_fide:
        raise TrialError("NO_BONA_FIDE_PRESENTATIONS")
    species: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for t in attacks:
        name = t.get("species")
        if not isinstance(name, str) or not name.strip():
            raise TrialError("ATTACK_SPECIES_MISSING")
        species[name].append(t)
    per_species = {
        name: measure(sum(1 for t in group if _flag(t, "accepted")), len(group), confidence)
        for name, group in sorted(species.items())
    }
    worst = max(per_species, key=lambda n: (per_species[n]["value"], per_species[n]["ci_high"]))
    return {
        "apcer": {**per_species[worst], "species": worst},
        "apcer_by_species": per_species,
        "bpcer": measure(
            sum(1 for t in bona_fide if not _flag(t, "accepted")), len(bona_fide), confidence
        ),
        "failure_to_acquire": _failure_to_acquire(rows, confidence),
    }


def face_match_measures(
    trials: Iterable[Mapping[str, Any]], confidence: float = 0.95
) -> dict[str, Any]:
    """FMR (impostor comparisons accepted), FNMR (genuine comparisons rejected) and failure to acquire, from raw comparison trials."""
    rows = list(trials)
    if not rows:
        raise TrialError("NO_TRIALS")
    acquired = [t for t in rows if _flag(t, "acquired")]
    genuine = [t for t in acquired if _flag(t, "genuine")]
    impostor = [t for t in acquired if not _flag(t, "genuine")]
    if not genuine:
        raise TrialError("NO_GENUINE_COMPARISONS")
    if not impostor:
        raise TrialError("NO_IMPOSTOR_COMPARISONS")
    return {
        "fmr": measure(sum(1 for t in impostor if _flag(t, "accepted")), len(impostor), confidence),
        "fnmr": measure(
            sum(1 for t in genuine if not _flag(t, "accepted")), len(genuine), confidence
        ),
        "failure_to_acquire": _failure_to_acquire(rows, confidence),
    }


def cohort_differences(
    trials: Iterable[Mapping[str, Any]],
    error_of: Any,
    confidence: float = 0.95,
) -> dict[str, Any]:
    """Per-cohort error rates and every pair of cohorts compared, for a fairness review. `error_of(trial)` returns True (an error),
    False (a correct outcome) or None (the trial does not count for this measure).

    Two cohorts are marked `distinguishable` only when their intervals do not overlap, which is a conservative test: a small sample is NEVER
    reported as 'no difference', only as 'not distinguishable at this sample size', and `smallest_cohort_trials` is reported so that can be seen.
    Whether a gap is acceptable is a policy decision this function does not make."""
    counts: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for t in trials:
        cohort = t.get("cohort")
        if not isinstance(cohort, str) or not cohort.strip():
            raise TrialError("COHORT_MISSING")
        outcome = error_of(t)
        if outcome is None:
            continue
        counts[cohort][1] += 1
        counts[cohort][0] += 1 if outcome else 0
    if len(counts) < 2:
        raise TrialError("NEED_AT_LEAST_TWO_COHORTS")
    rates = {c: measure(e, n, confidence) for c, (e, n) in sorted(counts.items())}
    pairs = []
    names = sorted(rates)
    for i, a in enumerate(names):
        for b in names[i + 1 :]:
            ra, rb = rates[a], rates[b]
            pairs.append(
                {
                    "cohorts": [a, b],
                    "difference": abs(ra["value"] - rb["value"]),
                    "ratio_higher_to_lower": (
                        max(ra["value"], rb["value"]) / min(ra["value"], rb["value"])
                        if min(ra["value"], rb["value"]) > 0
                        else None
                    ),
                    "distinguishable": ra["ci_low"] > rb["ci_high"] or rb["ci_low"] > ra["ci_high"],
                }
            )
    return {
        "cohorts": rates,
        "pairs": pairs,
        "smallest_cohort_trials": min(r["trials"] for r in rates.values()),
    }


def pad_cohort_differences(
    trials: Iterable[Mapping[str, Any]], confidence: float = 0.95
) -> dict[str, Any]:
    """Cohort differences for the two PAD errors (bona fide rejected, attack accepted), among acquired presentations."""

    def bpcer(t: Mapping[str, Any]) -> bool | None:
        if not _flag(t, "acquired") or _flag(t, "attack"):
            return None
        return not _flag(t, "accepted")

    def apcer(t: Mapping[str, Any]) -> bool | None:
        if not _flag(t, "acquired") or not _flag(t, "attack"):
            return None
        return _flag(t, "accepted")

    rows = list(trials)
    return {
        "bpcer": cohort_differences(rows, bpcer, confidence),
        "apcer": cohort_differences(rows, apcer, confidence),
    }
