"""Choosing a face-match threshold from measurements (CR-130), instead of taking an engine author's default.

A pure module over similarity scores. Two kinds of comparison are measured:

    same        two photographs of the same person (a "genuine" pair)
    different   two photographs of different people (an "impostor" pair)

The two errors a threshold trades off:

    FMR   false match rate: the share of `different` pairs scored at or above the threshold (a stranger let through)
    FNMR  false non-match rate: the share of `same` pairs scored below it (the right person turned away)

An owner picks the false match rate they will accept (for example 1 in 1,000), and the threshold follows from the measurements. The report says how many comparisons stand behind it, because a
threshold from a few pairs proves nothing: with no stranger let through in n trials, the true rate is still only known to be below about 3/n (the "rule of three", 95% confidence).

What this is not: an evaluation. Pairs from one public set, or from the owner's friends, are not the document-photograph-against-live-selfie population the route will meet, and nothing here measures
presentation attacks (a photograph held to the camera). A calibrated threshold is a necessary record for the gate, never a sufficient one."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

DEFAULT_TARGETS: tuple[float, ...] = (0.1, 0.01, 0.001)
MIN_PER_KIND = 2


class CalibrationError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Pair:
    score: float
    same: bool


def split(pairs: Sequence[Pair]) -> tuple[list[float], list[float]]:
    """(same-person scores, different-person scores). Refuses a set that cannot be measured."""
    same = [p.score for p in pairs if p.same]
    different = [p.score for p in pairs if not p.same]
    if len(same) < MIN_PER_KIND:
        raise CalibrationError("TOO_FEW_SAME_PERSON_PAIRS")
    if len(different) < MIN_PER_KIND:
        raise CalibrationError("TOO_FEW_DIFFERENT_PERSON_PAIRS")
    if any(not math.isfinite(p.score) for p in pairs):
        raise CalibrationError("SCORE_NOT_A_NUMBER")
    return same, different


def rates(
    same: Sequence[float], different: Sequence[float], threshold: float
) -> tuple[float, float]:
    """(FMR, FNMR) at a threshold: a score at or above it is a match."""
    fmr = sum(1 for s in different if s >= threshold) / len(different)
    fnmr = sum(1 for s in same if s < threshold) / len(same)
    return fmr, fnmr


def wilson_upper(errors: int, trials: int, z: float = 1.96) -> float:
    """The 95% upper bound on a true error rate seen as `errors` in `trials`. With zero errors it is close to 3/trials."""
    if trials <= 0:
        return 1.0
    p = errors / trials
    z2 = z * z
    centre = p + z2 / (2 * trials)
    margin = z * math.sqrt(p * (1 - p) / trials + z2 / (4 * trials * trials))
    return min(1.0, (centre + margin) / (1 + z2 / trials))


def threshold_for_fmr(different: Sequence[float], target: float) -> float:
    """The lowest threshold that lets through at most `target` of the different-person pairs seen."""
    if not 0 < target < 1:
        raise CalibrationError("TARGET_OUT_OF_RANGE")
    ordered = sorted(different, reverse=True)
    allowed = int(target * len(ordered))
    if allowed >= len(ordered):
        return min(ordered)
    # strictly above the (allowed+1)-th highest impostor score, so at most `allowed` impostors reach it
    return math.nextafter(ordered[allowed], math.inf)


def equal_error_rate(same: Sequence[float], different: Sequence[float]) -> tuple[float, float]:
    """(threshold, rate) where FMR and FNMR are closest: a single number to compare engines by, never to deploy on."""
    best = (0.0, 1.0, 2.0)  # threshold, rate, gap
    for t in sorted({*same, *different}):
        fmr, fnmr = rates(same, different, t)
        gap = abs(fmr - fnmr)
        if gap < best[2]:
            best = (t, (fmr + fnmr) / 2, gap)
    return best[0], best[1]


def report(pairs: Sequence[Pair], targets: Sequence[float] = DEFAULT_TARGETS) -> dict[str, object]:
    """The numbers an owner needs to choose a threshold, and how far to trust them."""
    same, different = split(pairs)
    rows: list[dict[str, object]] = []
    for target in targets:
        t = threshold_for_fmr(different, target)
        fmr, fnmr = rates(same, different, t)
        errors = round(fmr * len(different))
        rows.append(
            {
                "target_fmr": target,
                "threshold": round(t, 4),
                "fmr": round(fmr, 6),
                "fmr_upper_95": round(wilson_upper(errors, len(different)), 6),
                "fnmr": round(fnmr, 6),
                "fnmr_upper_95": round(wilson_upper(round(fnmr * len(same)), len(same)), 6),
                # at least ceil(3/target) different-person pairs, or "no strangers let through" proves nothing at that target
                "enough_pairs": len(different) >= math.ceil(3 / target),
            }
        )
    eer_t, eer = equal_error_rate(same, different)
    return {
        "same_pairs": len(same),
        "different_pairs": len(different),
        "same_mean": round(sum(same) / len(same), 4),
        "different_mean": round(sum(different) / len(different), 4),
        "same_min": round(min(same), 4),
        "different_max": round(max(different), 4),
        "equal_error_rate": round(eer, 6),
        "equal_error_threshold": round(eer_t, 4),
        "operating_points": rows,
        "separable": min(same) > max(different),
        "note": "Measured on the pairs supplied only. Not an evaluation of the route: document photographs against live selfies, other populations and presentation attacks are not covered.",
    }
