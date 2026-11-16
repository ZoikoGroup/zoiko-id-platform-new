"""Load the Travel Rule counterparty registry (`backend/config/product-lines/vasp-registry.yaml`; ZID_PLATFORM_CONFIG overrides the config root).

Shipped empty: no provider and no threshold is invented. A file that does not parse stops the service rather than trusting the wrong providers.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from zid_platform.modules.verification.domain.travel_rule import VaspRegistry, parse_registry

CONFIG = Path(
    os.environ.get("ZID_PLATFORM_CONFIG") or Path(__file__).resolve().parents[6] / "config"
)
FILE = "product-lines/vasp-registry.yaml"


@lru_cache(maxsize=1)
def vasp_registry() -> VaspRegistry:
    doc: dict[str, Any] = yaml.safe_load((CONFIG / FILE).read_text(encoding="utf-8")) or {}
    return parse_registry(doc)


__all__ = ["vasp_registry"]
