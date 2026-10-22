"""Public Protocol interface of the verification module. Other modules import ONLY this."""

from zid_platform.modules.verification.application.service import (
    assurance,
    claim_matches,
    fresh_reproof,
    record_presentation,
    revoke,
)

__all__ = ["assurance", "claim_matches", "fresh_reproof", "record_presentation", "revoke"]
