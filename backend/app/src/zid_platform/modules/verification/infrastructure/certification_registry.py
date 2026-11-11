"""Load the certified-provider registry (`backend/config/certifications`; ZID_PLATFORM_CONFIG overrides the config root).

Versioned configuration. The shipped file lists NO certification: Zoiko iD holds none today, so no claim to be a certified UK
digital verification provider or a qualified trust service provider can be backed (DTV-14, DTV-15). A record is added only with
its certificate reference, scheme version and dates, after the owners have the independent evidence.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from zid_platform.modules.verification.domain.certification import CertRegistry, parse

CONFIG = Path(
    os.environ.get("ZID_PLATFORM_CONFIG") or Path(__file__).resolve().parents[6] / "config"
)
FILE = "certifications/certifications.yaml"


@lru_cache(maxsize=1)
def certification_registry() -> CertRegistry:
    doc: dict[str, Any] = yaml.safe_load((CONFIG / FILE).read_text(encoding="utf-8")) or {}
    return parse(doc)


__all__ = ["CONFIG", "certification_registry"]
