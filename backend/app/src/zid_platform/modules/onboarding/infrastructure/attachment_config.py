"""Load the approval-attachment policy (`backend/config/approvals/attachment-policy.yaml`; ZID_PLATFORM_CONFIG overrides the root)."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml

from zid_platform.modules.organization.ports import ProofPolicy, parse_proof_policy

CONFIG = Path(
    os.environ.get("ZID_PLATFORM_CONFIG") or Path(__file__).resolve().parents[6] / "config"
)


@lru_cache(maxsize=1)
def attachment_policy() -> ProofPolicy:
    """The same shape as the proof policy of a company claim (types, kinds, size, limit, retention), read from its own file."""
    doc = yaml.safe_load(
        (CONFIG / "approvals" / "attachment-policy.yaml").read_text(encoding="utf-8")
    )
    return parse_proof_policy(doc or {})
