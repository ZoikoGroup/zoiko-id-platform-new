"""Loader for config/self-run-verification/self-run.yaml (CR-095). Read on each call: the file is small, and a change must take effect without a restart."""

from __future__ import annotations

import os
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import yaml

from zid_platform.modules.verification.domain.biometric_engine import EngineGate, engine_gate
from zid_platform.modules.verification.infrastructure import engine_register

FILE = Path(
    os.environ.get("ZID_SELF_RUN_CONFIG")
    or Path(__file__).resolve().parents[6] / "config" / "self-run-verification" / "self-run.yaml"
)


def raw() -> dict[str, Any]:
    loaded = yaml.safe_load(FILE.read_text(encoding="utf-8")) or {}
    return loaded if isinstance(loaded, dict) else {}


def accepted_engine(today: date | None = None) -> EngineGate | None:
    """None while the engine is off. Raises EngineGateError when it is switched on but its references are not complete."""
    return engine_gate(
        raw(), today=today or datetime.now(UTC).date(), register=engine_register.register()
    )
