"""Load the country privacy overlays (`backend/config/regulatory/privacy-overlays.yaml`; ZID_PLATFORM_CONFIG overrides the config root; CR-079).

Versioned configuration. The shipped file lists NO country: an approved overlay is added only by the DPO and Legal.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from zid_platform.modules.verification.domain.privacy_overlay import Overlay, parse

CONFIG = Path(
    os.environ.get("ZID_PLATFORM_CONFIG") or Path(__file__).resolve().parents[6] / "config"
)
FILE = "regulatory/privacy-overlays.yaml"


@lru_cache(maxsize=1)
def privacy_overlays() -> dict[str, Overlay]:
    doc: dict[str, Any] = yaml.safe_load((CONFIG / FILE).read_text(encoding="utf-8")) or {}
    return parse(doc)
