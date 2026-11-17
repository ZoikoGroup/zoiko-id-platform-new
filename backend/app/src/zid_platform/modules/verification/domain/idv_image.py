"""Printed-document image checks, as rules over measurements (ZID-DTV-WF-03 E07; CR-110). Pure domain, no imaging library.

The measurements come from the imaging sidecar (`tools/ocr-sidecar`, `/inspect`); this module only decides what they mean. What it can and cannot say:
- QUALITY checks (resolution, sharpness, glare, exposure) say whether the capture is good enough to be judged. A failure asks for a better capture: it is not a finding about the document.
- ELA_ANOMALY is a screening heuristic (re-compression residue), not proof of tampering. A hot result is a reason for a person to look, never a rejection, and a clean result is not authenticity.
- There is NO template or security-feature check here (hologram, microprint, UV, laser engraving, font and layout of a real issuer). Those need genuine specimens of each document and an evaluation against them, which do not
  exist in this repository, so no result named TEMPLATE, TAMPER or PHOTO_SUBSTITUTION is ever produced. A market that requires one of them gets "not run", so its decision is INDETERMINATE and never an approval.
The thresholds are PROPOSED defaults, not measured against any document population."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

PRODUCED = (
    "RESOLUTION",
    "SHARPNESS",
    "GLARE",
    "EXPOSURE",
    "ELA_ANOMALY",
)  # the only check names this module ever produces


@dataclass(frozen=True, slots=True)
class Thresholds:
    min_short_side: int = 720
    min_sharpness: float = 60.0  # variance of an edge-filtered grey image
    max_glare: float = 0.02  # fraction of near-white pixels
    max_dark: float = 0.5  # fraction of near-black pixels
    ela_clean: float = (
        0.01  # fraction of re-compression hot pixels at or below which nothing is flagged
    )
    ela_hot: float = 0.05  # above this, a person should look


@dataclass(frozen=True, slots=True)
class Metrics:
    width: int
    height: int
    sharpness: float
    glare_fraction: float
    dark_fraction: float
    ela_hot_fraction: float | None  # None: not a JPEG, so the heuristic could not run
    image_format: str


def parse_metrics(raw: Mapping[str, Any]) -> Metrics | None:
    """The sidecar's answer as Metrics, or None if any number is missing or not a sensible number (an unreadable answer is never a pass)."""
    try:
        w, h = int(raw["width"]), int(raw["height"])
        nums = [float(raw[k]) for k in ("sharpness", "glare_fraction", "dark_fraction")]
        ela = raw.get("ela_hot_fraction")
        ela_f = None if ela is None else float(ela)
        fmt = str(raw.get("format", ""))
    except (KeyError, TypeError, ValueError):
        return None
    if (
        w <= 0
        or h <= 0
        or any(not math.isfinite(n) or n < 0 for n in nums)
        or (ela_f is not None and (not math.isfinite(ela_f) or not 0 <= ela_f <= 1))
    ):
        return None
    return Metrics(w, h, nums[0], nums[1], nums[2], ela_f, fmt)


DEFAULTS = Thresholds()


def checks(m: Metrics, t: Thresholds = DEFAULTS) -> dict[str, str]:
    out = {
        "RESOLUTION": "PASS" if min(m.width, m.height) >= t.min_short_side else "FAIL",
        "SHARPNESS": "PASS" if m.sharpness >= t.min_sharpness else "FAIL",
        "GLARE": "PASS" if m.glare_fraction <= t.max_glare else "FAIL",
        "EXPOSURE": "PASS" if m.dark_fraction <= t.max_dark else "FAIL",
    }
    if m.ela_hot_fraction is None:
        out["ELA_ANOMALY"] = "INCONCLUSIVE"
    elif m.ela_hot_fraction <= t.ela_clean:
        out["ELA_ANOMALY"] = "PASS"
    elif m.ela_hot_fraction <= t.ela_hot:
        out["ELA_ANOMALY"] = "INCONCLUSIVE"
    else:
        out["ELA_ANOMALY"] = "FAIL"
    return out


# ----------------------------------------------------------------------------------------------------------- reading the printed MRZ

_MRZ_LINE = re.compile(r"[A-Z0-9<]{30,44}")
_OCR_FIXES = str.maketrans({" ": "", "«": "<", "‹": "<", "K": "K"})


def mrz_lines(ocr_text: str) -> list[str]:
    """The machine-readable lines in a block of OCR text: upper-cased, spaces removed, only lines of a plausible length and alphabet. OCR errors are not guessed away: if a line does not parse and its check digits do not
    hold, the comparison simply fails."""
    lines = []
    for raw in ocr_text.upper().splitlines():
        s = raw.translate(_OCR_FIXES).strip()
        if _MRZ_LINE.fullmatch(s) and s.count("<") >= 2:
            lines.append(s)
    return lines[-3:]
