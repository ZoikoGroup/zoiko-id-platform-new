"""Load the proof of address policy (`backend/config/address-proof/policy.yaml`; ZID_PLATFORM_CONFIG overrides the config root). Shipped as a SAMPLE."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from zid_platform.modules.verification.domain.address import ProofPolicy, parse_policy

CONFIG = Path(
    os.environ.get("ZID_PLATFORM_CONFIG") or Path(__file__).resolve().parents[6] / "config"
)
FILE = "address-proof/policy.yaml"


@lru_cache(maxsize=1)
def proof_policy() -> ProofPolicy:
    doc: dict[str, Any] = yaml.safe_load((CONFIG / FILE).read_text(encoding="utf-8")) or {}
    return parse_policy(doc)


__all__ = ["proof_policy"]
