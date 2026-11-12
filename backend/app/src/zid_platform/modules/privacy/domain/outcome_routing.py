"""Where a provider's biometric or document outcome goes next (ZID-PRI-004 sections 06, 08 and 14; AT-010, AT-012 to AT-014). Pure: no I/O.

The provider reports bands, never a decision. This module decides only what the PLATFORM does with them, and holds four rules that no band can
override:

- a good match never approves anyone: the best outcome is "continue to the next separate check" (identity is not a licence, not a right to let);
- a doubtful PAD band, a suspected injected feed or an identity doubt stops autonomous progress and goes to an authorized person (AT-010);
- a high-impact decision resting on a match, whichever way it points, is held for a person and a correction route is offered (AT-013);
- no biometric band alone ever refuses a person: a non-match or a poor capture means retry, a non-biometric alternative or a person, with no penalty
  and never the word fraud (AT-012, AT-014).

Unknown or missing bands are treated as the cautious route, not as a pass. No threshold or percentage is set here: the bands come from the
provider's own evaluated operating points, which the owner records (`model_governance`).
"""

from __future__ import annotations

from dataclasses import dataclass

MATCH = ("MATCH", "NO_MATCH", "UNCERTAIN")
PAD = ("LOW_RISK", "ELEVATED", "HIGH_RISK")


@dataclass(frozen=True, slots=True)
class Route:
    route: str  # CONTINUE_TO_NEXT_CHECK | HUMAN_REVIEW | RETRY_OR_ALTERNATIVE
    reasons: tuple[str, ...]
    offer_alternative: bool
    offer_correction_route: bool
    penalty: bool = False  # never true: kept so a test can state it
    may_say_fraud: bool = False  # never true


def route(
    *,
    match_band: str | None,
    pad_band: str | None,
    injection_suspected: bool,
    capture_quality_ok: bool,
    high_impact: bool,
) -> Route:
    reasons: list[str] = []
    if injection_suspected:
        reasons.append("INJECTED_MEDIA_SUSPECTED")
    if pad_band not in PAD:
        reasons.append("PAD_BAND_MISSING_OR_UNKNOWN")
    elif pad_band != "LOW_RISK":
        reasons.append(f"PAD_{pad_band}")
    if (
        reasons
    ):  # AT-010: autonomous progress stops; a person decides, and the person is offered another way
        return Route("HUMAN_REVIEW", tuple(reasons), True, True)
    if not capture_quality_ok:  # AT-012: a poor camera is not an accusation
        return Route("RETRY_OR_ALTERNATIVE", ("CAPTURE_QUALITY_LOW",), True, False)
    if match_band not in MATCH:
        return Route("HUMAN_REVIEW", ("MATCH_BAND_MISSING_OR_UNKNOWN",), True, True)
    if (
        high_impact
    ):  # AT-013: a match or a non-match behind a significant decision is held for a person
        return Route("HUMAN_REVIEW", (f"HIGH_IMPACT_{match_band}",), True, True)
    if match_band == "MATCH":
        return Route("CONTINUE_TO_NEXT_CHECK", ("MATCH_IS_NOT_APPROVAL",), False, False)
    if match_band == "NO_MATCH":  # AT-014: retry or contest without penalty
        return Route("RETRY_OR_ALTERNATIVE", ("NO_MATCH_IS_NOT_A_REFUSAL",), True, True)
    return Route("RETRY_OR_ALTERNATIVE", ("MATCH_UNCERTAIN",), True, False)
