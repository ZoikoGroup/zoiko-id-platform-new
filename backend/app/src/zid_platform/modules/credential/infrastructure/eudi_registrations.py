"""Load Zoiko's EUDI Wallet relying-party registrations (`backend/config/regulatory/eudi-wallet-registrations.yaml`; CR-080).

Versioned configuration. The shipped file lists NO registration: Zoiko iD is not a registered wallet-relying party anywhere yet.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from zid_platform.modules.credential.domain.eudi import Registration, parse

CONFIG = Path(
    os.environ.get("ZID_PLATFORM_CONFIG") or Path(__file__).resolve().parents[6] / "config"
)
FILE = "regulatory/eudi-wallet-registrations.yaml"


@lru_cache(maxsize=1)
def eudi_registrations() -> tuple[Registration, ...]:
    doc: dict[str, Any] = yaml.safe_load((CONFIG / FILE).read_text(encoding="utf-8")) or {}
    return tuple(parse(doc))
