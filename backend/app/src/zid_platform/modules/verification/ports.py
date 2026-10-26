"""Public Protocol interface of the verification module. Other modules import ONLY this."""

from zid_platform.modules.verification.application.baseline import seed_representative_proofing
from zid_platform.modules.verification.application.service import (
    assurance,
    claim_matches,
    fresh_reproof,
    reconstruct_decision,
    record_presentation,
    revoke,
    shared_assertions,
)

__all__ = [
    "assurance",
    "claim_matches",
    "fresh_reproof",
    "reconstruct_decision",
    "record_presentation",
    "revoke",
    "seed_representative_proofing",
    "shared_assertions",
]
