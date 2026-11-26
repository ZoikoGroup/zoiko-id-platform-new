"""Loader for config/developer/api-versions.yaml and its immutable snapshots (ZID-CP-WF10 WF-10.05; CR-173). Read on each call."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[6]  # backend/
REPO = ROOT.parent
CONFIG = Path(os.environ.get("ZID_PLATFORM_CONFIG") or ROOT / "config")
FILE = Path(os.environ.get("ZID_API_VERSIONS") or CONFIG / "developer" / "api-versions.yaml")


def register() -> list[dict[str, Any]]:
    loaded = yaml.safe_load(FILE.read_text(encoding="utf-8")) or {}
    items = loaded.get("releases") if isinstance(loaded, dict) else None
    return [dict(x) for x in items] if isinstance(items, list) else []


def snapshot_text(snapshot_path: str) -> str | None:
    """The snapshot file named by the register, only if it lies inside the repository's contracts folder."""
    p = (REPO / snapshot_path).resolve()
    if not str(p).startswith(str((REPO / "contracts").resolve())) or not p.is_file():
        return None
    return p.read_text(encoding="utf-8")
