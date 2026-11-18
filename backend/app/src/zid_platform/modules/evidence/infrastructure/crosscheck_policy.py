"""Load the chip cross-check gate policy (`backend/config/self-run-verification/chip-crosscheck-gate.yaml`; E03; CR-123). It ships with NO policy, so the gate never passes until the security and QA owners record one. Strict: an unknown key or a bad number refuses the whole file."""

from __future__ import annotations

import os
from pathlib import Path

import yaml

from zid_platform.modules.evidence.domain.chip_crosscheck import (
    ACCESS,
    REFERENCES,
    CrosscheckError,
    GatePolicy,
)

FILE = Path(
    os.environ.get("ZID_CROSSCHECK_POLICY")
    or Path(__file__).resolve().parents[6]
    / "config"
    / "self-run-verification"
    / "chip-crosscheck-gate.yaml"
)
_KEYS = {
    "min_documents",
    "min_devices",
    "required_access",
    "min_runs_per_access",
    "required_references",
    "min_comparable_passive_auth_share",
}


def parse(doc: object) -> GatePolicy | None:
    policy = (doc or {}).get("policy") if isinstance(doc, dict) else None
    if policy is None:
        return None
    if (
        not isinstance(policy, dict)
        or set(policy) - _KEYS
        or not {
            "min_documents",
            "min_devices",
            "required_access",
            "min_runs_per_access",
            "required_references",
        }
        <= set(policy)
    ):
        raise CrosscheckError("POLICY_INVALID", "unknown or missing keys")
    for key in ("min_documents", "min_devices", "min_runs_per_access"):
        if not isinstance(policy[key], int) or isinstance(policy[key], bool) or policy[key] < 1:
            raise CrosscheckError("POLICY_INVALID", f"{key} is a whole number of at least 1")
    access, refs = (
        frozenset(str(a).upper() for a in policy["required_access"]),
        frozenset(str(r).upper() for r in policy["required_references"]),
    )
    if not access or access - ACCESS or not refs or refs - REFERENCES:
        raise CrosscheckError(
            "POLICY_INVALID", "required_access and required_references name known values"
        )
    share = policy.get("min_comparable_passive_auth_share", 1.0)
    if not isinstance(share, (int, float)) or isinstance(share, bool) or not 0 < share <= 1:
        raise CrosscheckError(
            "POLICY_INVALID", "the passive authentication share is above 0 and at most 1"
        )
    return GatePolicy(
        policy["min_documents"],
        policy["min_devices"],
        access,
        policy["min_runs_per_access"],
        refs,
        float(share),
    )


def load() -> GatePolicy | None:
    return parse(yaml.safe_load(FILE.read_text(encoding="utf8"))) if FILE.exists() else None
