"""Load the progressive-verification policy (`backend/config/verification/tier-policy.yaml`; ZID_PLATFORM_CONFIG overrides the config root).
Versioned configuration, validated on load: a policy that asks for more at registration or lets a non-proof signal count does not start."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml

from zid_platform.modules.verification.domain.tiers import TierPolicy, parse

CONFIG = Path(os.environ.get("ZID_PLATFORM_CONFIG") or Path(__file__).resolve().parents[6] / "config")


@lru_cache(maxsize=1)
def tier_policy() -> TierPolicy:
    return parse(yaml.safe_load((CONFIG / "verification" / "tier-policy.yaml").read_text(encoding="utf-8")))
