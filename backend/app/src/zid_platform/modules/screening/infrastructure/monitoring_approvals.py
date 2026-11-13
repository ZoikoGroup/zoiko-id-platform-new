"""Load the monitoring approvals (`backend/config/screening/monitoring-approvals.yaml`; ZID_PLATFORM_CONFIG overrides the config root; CR-081).

Versioned configuration. The shipped file lists NO approval: there is no background monitoring until Legal and Compliance approve one.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from zid_platform.modules.screening.domain.monitoring_scope import MonitoringApproval, parse

CONFIG = Path(
    os.environ.get("ZID_PLATFORM_CONFIG") or Path(__file__).resolve().parents[6] / "config"
)
FILE = "screening/monitoring-approvals.yaml"


@lru_cache(maxsize=1)
def monitoring_approvals() -> tuple[MonitoringApproval, ...]:
    doc: dict[str, Any] = yaml.safe_load((CONFIG / FILE).read_text(encoding="utf-8")) or {}
    return tuple(parse(doc))
