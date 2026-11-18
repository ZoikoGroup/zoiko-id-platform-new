"""Load the WF-05 check method adapters (`config/verification/check-adapters.yaml`) and the retention rule for each data class they create
(`config/privacy/check-data-classes.yaml`). ZID_PLATFORM_CONFIG overrides the config root (CR-112)."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from zid_platform.modules.verification.domain.check_methods import (
    Adapter,
    CheckMethod,
    RetentionRule,
    parse_adapters,
    parse_retention,
)

CONFIG = Path(
    os.environ.get("ZID_PLATFORM_CONFIG") or Path(__file__).resolve().parents[6] / "config"
)
ADAPTERS_FILE = "verification/check-adapters.yaml"
RETENTION_FILE = "privacy/check-data-classes.yaml"


def _load(name: str) -> dict[str, Any]:
    return yaml.safe_load((CONFIG / name).read_text(encoding="utf-8")) or {}


@lru_cache(maxsize=1)
def adapters() -> dict[CheckMethod, Adapter]:
    return parse_adapters(_load(ADAPTERS_FILE))


@lru_cache(maxsize=1)
def retention() -> dict[str, RetentionRule]:
    return parse_retention(_load(RETENTION_FILE))


__all__ = ["ADAPTERS_FILE", "CONFIG", "RETENTION_FILE", "adapters", "retention"]
