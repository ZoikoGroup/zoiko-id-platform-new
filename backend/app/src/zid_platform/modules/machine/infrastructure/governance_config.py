"""Loader for config/agent-governance/governance.yaml (ZID-AG-WF-002; CR-105). Read once; a missing or invalid file stops start-up use."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import timedelta
from functools import lru_cache
from pathlib import Path

import yaml

from zid_platform.modules.machine.domain.governance import QuotaLimits

FILE = Path(
    os.environ.get("ZID_AGENT_GOVERNANCE_CONFIG")
    or Path(__file__).resolve().parents[6] / "config" / "agent-governance" / "governance.yaml"
)


@dataclass(frozen=True, slots=True)
class GovernanceConfig:
    version: str
    status: str
    platform_quota_ceiling: QuotaLimits
    approved_currencies: dict[str, int]
    reservation_ttl: timedelta
    telemetry_max_age: timedelta
    approval_ttl: timedelta
    restore_health_max_age: timedelta
    emergency_stop_target_seconds: int
    price_version: str
    alert_sla: dict[str, timedelta]
    ai_explain_reservation: tuple[str, str]


@lru_cache(maxsize=1)
def config() -> GovernanceConfig:
    raw = yaml.safe_load(FILE.read_text(encoding="utf-8"))
    ceiling = QuotaLimits.from_dict(raw["platform_quota_ceiling"])
    problems = ceiling.problems()
    currencies = {str(k): int(v) for k, v in raw["approved_currencies"].items()}
    if problems or not currencies or any(v < 0 or v > 4 for v in currencies.values()):
        raise ValueError(f"agent governance configuration is invalid: {problems or 'currencies'}")
    return GovernanceConfig(
        version=str(raw["version"]),
        status=str(raw["status"]),
        platform_quota_ceiling=ceiling,
        approved_currencies=currencies,
        reservation_ttl=timedelta(seconds=int(raw["reservation_ttl_seconds"])),
        telemetry_max_age=timedelta(seconds=int(raw["telemetry_max_age_seconds"])),
        approval_ttl=timedelta(
            hours=min(24, int(raw["approval_ttl_hours"]))
        ),  # never longer than the 24 h the document allows
        restore_health_max_age=timedelta(seconds=int(raw["restore_health_max_age_seconds"])),
        emergency_stop_target_seconds=int(raw["emergency_stop_target_seconds"]),
        price_version=str(raw["price_version"]),
        alert_sla={k: timedelta(minutes=int(v)) for k, v in raw["alert_sla_minutes"].items()},
        ai_explain_reservation=(
            str(raw["ai_explain_reservation"]["amount"]),
            str(raw["ai_explain_reservation"]["currency"]),
        ),
    )
