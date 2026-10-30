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
from zid_platform.modules.verification.domain.countries import (
    SANDBOX_ENVIRONMENTS,
    Availability,
    Capability,
    CountryError,
    availability,
    require,
)
from zid_platform.modules.verification.infrastructure.country_registry import registry

__all__ = [
    "SANDBOX_ENVIRONMENTS",
    "Availability",
    "Capability",
    "CountryError",
    "assurance",
    "availability",
    "claim_matches",
    "fresh_reproof",
    "reconstruct_decision",
    "record_presentation",
    "registry",
    "require",
    "revoke",
    "seed_representative_proofing",
    "shared_assertions",
]
