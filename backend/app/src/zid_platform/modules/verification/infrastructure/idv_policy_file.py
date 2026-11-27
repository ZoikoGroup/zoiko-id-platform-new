"""Loader for config/idv/market-policies.yaml (ZID-DTV-WF-03; CR-110). Read on each call so a change takes effect without a restart; a malformed file refuses every market, so nothing runs on a half-read file."""

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

import yaml

from zid_platform.modules.verification.domain.idv_policy import MarketRules, parse

FILE = Path(
    os.environ.get("ZID_IDV_POLICIES")
    or Path(__file__).resolve().parents[6] / "config" / "idv" / "market-policies.yaml"
)


def load() -> dict[str, MarketRules]:
    try:
        doc = yaml.safe_load(FILE.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        doc = {}
    return parse(doc)


# The built-in global policy: one reviewed file in the repository that serves any country with no policy of its own, ONLY when the owners' written acceptance
# is configured (ZID_IDV_GLOBAL_POLICY_ACK equal to GLOBAL_POLICY_ACK). It states, in the owners' own terms, the values a per-country policy carries. A country policy
# in market-policies.yaml always wins over it. The market key inside the file is GLOBAL.
GLOBAL_POLICY_ACK = "accept-global-idv-policy"
GLOBAL_MARKET = "GLOBAL"
GLOBAL_FILE = Path(
    os.environ.get("ZID_IDV_GLOBAL_POLICY")
    or Path(__file__).resolve().parents[6] / "config" / "idv" / "global-policy.yaml"
)


# ZID_IDV_OPEN_TO_ALL_COUNTRIES (set once at startup by the composition root): a country an operator has opened needs no recorded lawful basis or impact assessment either. The global
# policy then carries this label in both fields, so a case's record says plainly that no assessment stands behind the face comparison, instead of leaving the fields empty and the route refusing.
NO_COUNTRY_GATE_BASIS = "OPEN_TO_ALL_COUNTRIES"
_open_country = False


def configure_open_country(on: bool) -> None:
    global _open_country
    _open_country = on


def load_global() -> MarketRules | None:
    """The global policy, or None when the file is missing or has no GLOBAL entry. A malformed file raises PolicyError, so it refuses instead of running on a guess."""
    try:
        doc = yaml.safe_load(GLOBAL_FILE.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        return None
    rules = parse(doc).get(GLOBAL_MARKET)
    if rules is not None and _open_country:
        rules = replace(
            rules,
            basis=replace(
                rules.basis,
                lawful_basis=rules.basis.lawful_basis or NO_COUNTRY_GATE_BASIS,
                impact_assessment_ref=rules.basis.impact_assessment_ref or NO_COUNTRY_GATE_BASIS,
            ),
        )
    return rules
