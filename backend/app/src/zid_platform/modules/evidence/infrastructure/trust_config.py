"""Load the trust configuration (`backend/config/trust`; ZID_PLATFORM_CONFIG overrides the config root). An empty folder trusts nothing."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from zid_platform.modules.evidence.application.trust import TrustConfig, load_trust

CONFIG = Path(
    os.environ.get("ZID_PLATFORM_CONFIG") or Path(__file__).resolve().parents[6] / "config"
)


@lru_cache(maxsize=1)
def trust_config() -> TrustConfig:
    return load_trust(CONFIG / "trust")


__all__ = ["CONFIG", "trust_config"]
