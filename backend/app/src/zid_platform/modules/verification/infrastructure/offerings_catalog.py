"""Load the service-offerings catalog (`backend/config/offerings/service-offerings.yaml`; ZID_PLATFORM_CONFIG overrides the root; CR-088).

What each offering is and what it depends on. Where it is available is never configured: it is computed per country by
`domain/offerings.decide` from the same records the regulatory gates use.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from zid_platform.modules.verification.domain.offerings import Offering, parse

CONFIG = Path(
    os.environ.get("ZID_PLATFORM_CONFIG") or Path(__file__).resolve().parents[6] / "config"
)
FILE = "offerings/service-offerings.yaml"


@lru_cache(maxsize=1)
def offerings_catalog() -> tuple[Offering, ...]:
    doc: dict[str, Any] = yaml.safe_load((CONFIG / FILE).read_text(encoding="utf-8")) or {}
    return tuple(parse(doc))
