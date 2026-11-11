"""Load the service pack registry (`backend/config/service-packs`; ZID_PLATFORM_CONFIG overrides the config root).

Versioned configuration, like the country capability registry: a pack is switched on or off by editing the file, never code.
A file that fails validation is refused whole at load; there is no partial registry.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from zid_platform.modules.verification.domain.service_packs import (
    PackRegistry,
    parse,
    validate_registry,
)

CONFIG = Path(
    os.environ.get("ZID_PLATFORM_CONFIG") or Path(__file__).resolve().parents[6] / "config"
)
FILE = "service-packs/service-packs.yaml"


def load(doc: dict[str, Any]) -> PackRegistry:
    registry = parse(doc)
    validate_registry(
        registry
    )  # shape, dates, evidence and hash; source availability is checked at publish time
    return registry


@lru_cache(maxsize=1)
def pack_registry() -> PackRegistry:
    doc: dict[str, Any] = yaml.safe_load((CONFIG / FILE).read_text(encoding="utf-8")) or {}
    return load(doc)


__all__ = ["CONFIG", "load", "pack_registry"]
