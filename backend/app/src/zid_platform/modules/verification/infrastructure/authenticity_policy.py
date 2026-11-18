"""Load the printed-document authenticity gate policy (`backend/config/idv/authenticity-gate.yaml`; T24, T25; CR-125). It ships with NO policy, so the gate never passes until the security and QA owners record one. Strict: an unknown key or a bad number refuses the whole file."""

from __future__ import annotations

import os
from pathlib import Path

import yaml

from zid_platform.modules.verification.domain.authenticity_evaluation import (
    ATTACK_KINDS,
    EvaluationError,
    Policy,
)

FILE = Path(
    os.environ.get("ZID_AUTHENTICITY_POLICY")
    or Path(__file__).resolve().parents[6] / "config" / "idv" / "authenticity-gate.yaml"
)
_KEYS = {
    "min_genuine",
    "min_attacks",
    "max_attack_accept_rate",
    "max_genuine_reject_rate",
    "max_genuine_inconclusive_rate",
    "required_attack_kinds",
    "min_devices",
}


def _rate(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
        raise EvaluationError("POLICY_INVALID", f"{name} is a share from 0 to 1")
    return float(value)


def _count(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise EvaluationError("POLICY_INVALID", f"{name} is a whole number of at least 1")
    return value


def parse(doc: object) -> Policy | None:
    policy = doc.get("policy") if isinstance(doc, dict) else None
    if policy is None:
        return None
    if not isinstance(policy, dict) or set(policy) != _KEYS:
        raise EvaluationError("POLICY_INVALID", f"name exactly: {', '.join(sorted(_KEYS))}")
    kinds = frozenset(str(k).upper() for k in policy["required_attack_kinds"] or ())
    if not kinds or kinds - ATTACK_KINDS:
        raise EvaluationError(
            "POLICY_INVALID", "required_attack_kinds names one or more known attack kinds"
        )
    return Policy(
        _count(policy["min_genuine"], "min_genuine"), _count(policy["min_attacks"], "min_attacks"),
        _rate(policy["max_attack_accept_rate"], "max_attack_accept_rate"), _rate(policy["max_genuine_reject_rate"], "max_genuine_reject_rate"),
        _rate(policy["max_genuine_inconclusive_rate"], "max_genuine_inconclusive_rate"), kinds, _count(policy["min_devices"], "min_devices"),
    )  # fmt: skip


def load() -> Policy | None:
    return parse(yaml.safe_load(FILE.read_text(encoding="utf8"))) if FILE.exists() else None
