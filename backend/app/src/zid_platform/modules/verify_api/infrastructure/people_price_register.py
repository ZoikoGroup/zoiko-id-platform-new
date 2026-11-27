"""Loader for config/developer/verify-people-prices.yaml. Read on each call so a price change takes effect without a restart."""

from __future__ import annotations

import os
from pathlib import Path

import yaml

from zid_platform.modules.verify_api.domain import people_prices as pp

CONFIG = Path(
    os.environ.get("ZID_PLATFORM_CONFIG") or Path(__file__).resolve().parents[6] / "config"
)
FILE = Path(
    os.environ.get("ZID_VERIFY_PEOPLE_PRICES") or CONFIG / "developer" / "verify-people-prices.yaml"
)


def table() -> pp.Table:
    return pp.parse(yaml.safe_load(FILE.read_text(encoding="utf-8")))
