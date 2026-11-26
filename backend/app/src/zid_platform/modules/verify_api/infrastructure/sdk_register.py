"""Loader for config/developer/sdk-releases.yaml (ZID-CP-WF10 WF-10.06; CR-173). Read on each call, like the other registers, so a change takes effect without a restart."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

CONFIG = Path(os.environ.get("ZID_PLATFORM_CONFIG") or Path(__file__).resolve().parents[6] / "config")
FILE = Path(os.environ.get("ZID_SDK_RELEASES") or CONFIG / "developer" / "sdk-releases.yaml")


def register() -> list[dict[str, Any]]:
    loaded = yaml.safe_load(FILE.read_text(encoding="utf-8")) or {}
    items = loaded.get("sdks") if isinstance(loaded, dict) else None
    return [dict(x) for x in items] if isinstance(items, list) else []
