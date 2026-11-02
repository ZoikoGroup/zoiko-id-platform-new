"""Load the proof-document policy (`backend/config/kyb/proof-policy.yaml`; ZID_PLATFORM_CONFIG overrides the root)."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

import yaml

from zid_platform.modules.organization.domain.proof import ProofError, ProofPolicy
from zid_platform.modules.organization.infrastructure.kyb_config import CONFIG


def parse(doc: dict[str, Any]) -> ProofPolicy:
    types = tuple(str(t) for t in doc.get("types") or ())
    kinds = tuple(str(k) for k in doc.get("kinds") or ())
    if not types or not kinds:
        raise ProofError("PROOF_POLICY_INVALID", "the policy must name its types and kinds")
    max_bytes = int(doc.get("max_bytes", 0))
    if max_bytes <= 0:
        raise ProofError("PROOF_POLICY_INVALID", "max_bytes must be positive")
    return ProofPolicy(
        version=str(doc.get("version", "0")),
        max_bytes=max_bytes,
        types=types,
        kinds=kinds,
        per_case_limit=int(doc.get("per_case_limit", 20)),
        retention_days=int(doc["retention_days"]) if doc.get("retention_days") else None,
    )


@lru_cache(maxsize=1)
def proof_policy() -> ProofPolicy:
    return parse(
        yaml.safe_load((CONFIG / "kyb" / "proof-policy.yaml").read_text(encoding="utf-8")) or {}
    )
