"""Loader for config/self-run-verification/engine-register.yaml (WF01-011; CR-107): the licence and component register a live engine is checked against.
Read on each call, like the engine file, so a change takes effect without a restart."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

FILE = Path(
    os.environ.get("ZID_ENGINE_REGISTER")
    or Path(__file__).resolve().parents[6]
    / "config"
    / "self-run-verification"
    / "engine-register.yaml"
)


def register() -> dict[str, Any]:
    loaded = yaml.safe_load(FILE.read_text(encoding="utf-8")) or {}
    return loaded if isinstance(loaded, dict) else {}
