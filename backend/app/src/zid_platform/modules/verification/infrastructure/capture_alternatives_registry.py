"""Load the accessible capture alternatives (`backend/config/accessibility`; ZID_PLATFORM_CONFIG overrides the config root).

A configuration that leaves a country with no alternative does not load: the service refuses to start rather than ship "there is no other way".
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from zid_platform.modules.verification.domain.capture_alternatives import Registry, parse

CONFIG = Path(
    os.environ.get("ZID_PLATFORM_CONFIG") or Path(__file__).resolve().parents[6] / "config"
)
FILE = "accessibility/capture-alternatives.yaml"


@lru_cache(maxsize=1)
def capture_alternatives() -> Registry:
    doc: dict[str, Any] = yaml.safe_load((CONFIG / FILE).read_text(encoding="utf-8")) or {}
    return parse(doc)


__all__ = ["CONFIG", "capture_alternatives"]
