"""Load the finance policy (`backend/config/commercial/finance-policy.yaml`; ZID_PLATFORM_CONFIG overrides the root; slice L-14b, CR-054).

The policy's content hash is what the Founder and the CFO sign. A file that cannot place every amount, names a role twice in a tier or lets
one role both open and run does not load, so a typo can never quietly widen who may approve.
"""

from __future__ import annotations

import hashlib
import os
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from zid_platform.modules.commercial.domain.finance import (
    CommitmentPolicy,
    FinanceError,
    FinancePolicy,
    Instrument,
    Tier,
    to_minor,
    validate_policy,
)

CONFIG = Path(
    os.environ.get("ZID_PLATFORM_CONFIG") or Path(__file__).resolve().parents[6] / "config"
)


def _instrument(doc: dict[str, Any]) -> Instrument:
    tiers = tuple(
        Tier(
            None if t.get("max_usd") is None else to_minor(Decimal(str(t["max_usd"]))),
            tuple(str(r) for r in t["roles"]),
        )
        for t in doc["tiers"]
    )
    return Instrument(
        int(doc["window_days"]), tiers, int(doc["max_days"]) if doc.get("max_days") else None
    )


def _commitment(doc: dict[str, Any]) -> CommitmentPolicy:
    return CommitmentPolicy(
        int(doc["valid_days"]),
        to_minor(Decimal(str(doc["tier1_max_usd"]))),
        to_minor(Decimal(str(doc["tier2_max_usd"]))),
        int(doc["overlay_term_months"]),
        str(doc["enforcement"]),
    )


def parse(raw: bytes) -> FinancePolicy:
    doc = yaml.safe_load(raw.decode("utf-8")) or {}
    if doc.get("unit") != "USD":
        raise FinanceError("POLICY_INVALID", "the threshold unit is USD")
    policy = FinancePolicy(
        version=str(doc["version"]),
        content_hash=hashlib.sha256(raw).hexdigest(),
        fx_usd={str(k): Decimal(str(v)) for k, v in (doc.get("fx_usd") or {}).items()},
        request_roles=tuple(str(r) for r in doc["request_roles"]),
        execute_roles=tuple(str(r) for r in doc["execute_roles"]),
        request_valid_days=int(doc["request_valid_days"]),
        approval_valid_days=int(doc["approval_valid_days"]),
        credit_note=_instrument(doc["credit_note"]),
        pilot_credit=_instrument(doc["pilot_credit"]),
        commitment=_commitment(doc["commitment"]),
    )
    validate_policy(policy)
    return policy


@lru_cache(maxsize=1)
def finance_policy() -> FinancePolicy:
    return parse((CONFIG / "commercial" / "finance-policy.yaml").read_bytes())
