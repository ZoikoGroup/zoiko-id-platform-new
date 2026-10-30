"""Load the step-up tiers and the actions that need them (`backend/config/step-up`; ZID_PLATFORM_CONFIG overrides the config root).
Versioned configuration, validated on load: a catalogue that weakens S2 does not start."""

from __future__ import annotations

import os
from datetime import timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from zid_platform.modules.authentication.domain.step_up import (
    Catalogue,
    StepUpAction,
    StepUpError,
    Tier,
    TierPolicy,
    build,
)

CONFIG = Path(
    os.environ.get("ZID_PLATFORM_CONFIG") or Path(__file__).resolve().parents[6] / "config"
)


def parse(doc: dict[str, Any]) -> Catalogue:
    tiers: dict[Tier, TierPolicy] = {}
    for name, body in (doc.get("tiers") or {}).items():
        try:
            tier = Tier(str(name))
        except ValueError as e:
            raise StepUpError("CATALOGUE_INVALID", f"unknown tier {name}") from e
        age = body.get("max_age_seconds")
        tiers[tier] = TierPolicy(
            None if age is None else timedelta(seconds=int(age)),
            bool(body.get("single_use", False)),
            bool(body.get("phishing_resistant", False)),
            bool(body.get("action_bound", False)),
        )
    actions: list[StepUpAction] = []
    for a in doc.get("actions") or []:
        try:
            tier = Tier(str(a["tier"]))
            up = Tier(str(a["escalates_to"])) if a.get("escalates_to") else None
        except ValueError as e:
            raise StepUpError("CATALOGUE_INVALID", f"{a.get('id')}: unknown tier") from e
        actions.append(
            StepUpAction(
                str(a["id"]),
                str(a["group"]),
                str(a["action"]),
                tier,
                up,
                bool(a.get("recovery", False)),
                str(a["family"]),
                str(a.get("gate", "")),
                str(a.get("control", "")),
                tuple(str(x) for x in a.get("catalogue_actions") or ()),
            )
        )
    return build(
        str(doc.get("version", "")),
        tiers,
        timedelta(seconds=int(doc.get("challenge_ttl_seconds", 300))),
        bool(doc.get("s1_fallback_mfa", False)),
        frozenset(str(m) for m in doc.get("s2_forbidden_methods") or ()),
        frozenset(str(s) for s in doc.get("risk_escalation_states") or ()),
        actions,
    )


@lru_cache(maxsize=1)
def step_up_catalogue() -> Catalogue:
    doc: dict[str, Any] = (
        yaml.safe_load((CONFIG / "step-up" / "step-up-tiers.yaml").read_text(encoding="utf-8"))
        or {}
    )
    return parse(doc)


__all__ = ["parse", "step_up_catalogue"]
