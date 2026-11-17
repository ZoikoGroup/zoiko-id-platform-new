"""The purpose and risk tier a company's documents are filed under when the market pack is looked up (`config/kyb/market-defaults.yaml`, PROPOSED until the owners approve it)."""

from __future__ import annotations

import os
from pathlib import Path

import yaml

FILE = Path(
    os.environ.get("ZID_KYB_MARKET_DEFAULTS")
    or Path(__file__).resolve().parents[6] / "config" / "kyb" / "market-defaults.yaml"
)


def load() -> tuple[str, str]:
    """(purpose, risk tier). A missing or incomplete file gives the same proposed values, never an empty key that could match anything."""
    try:
        doc = yaml.safe_load(FILE.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        doc = {}
    purpose, tier = str(doc.get("purpose") or "ORG_ONBOARDING"), str(doc.get("risk_tier") or "LOW")
    return purpose, tier
