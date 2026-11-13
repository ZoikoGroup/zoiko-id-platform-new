"""Load the approved claim templates (`backend/config/regulatory/claim-templates.yaml`; ZID_PLATFORM_CONFIG overrides the config root; CR-077).

Versioned configuration. The shipped file lists NO template, so no regulated claim is publishable until Legal and the claims owner approve one.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from zid_platform.modules.verification.domain.regulated_claims import ClaimTemplate, parse

CONFIG = Path(
    os.environ.get("ZID_PLATFORM_CONFIG") or Path(__file__).resolve().parents[6] / "config"
)
FILE = "regulatory/claim-templates.yaml"


@lru_cache(maxsize=1)
def claim_templates() -> tuple[ClaimTemplate, ...]:
    doc: dict[str, Any] = yaml.safe_load((CONFIG / FILE).read_text(encoding="utf-8")) or {}
    return tuple(parse(doc))
