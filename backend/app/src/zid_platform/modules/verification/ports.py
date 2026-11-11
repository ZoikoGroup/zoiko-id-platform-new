"""Public Protocol interface of the verification module. Other modules import ONLY this."""

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.verification.application import policy_packs
from zid_platform.modules.verification.application.baseline import seed_representative_proofing
from zid_platform.modules.verification.application.service import (
    assurance,
    claim_matches,
    fresh_reproof,
    reconcile_delayed,
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
from zid_platform.modules.verification.domain.policy_packs import (
    APPROVER_ROLES,
    GATE_MODES,
    PACK_PURPOSE,
    GateDecision,
    LegalSource,
    Outcome,
    PackError,
    PackKey,
    PackStatus,
    PolicyPack,
    Resolution,
    Rules,
    SourceStatus,
    gate_decision,
    pack_hash,
    pack_needed,
)
from zid_platform.modules.verification.infrastructure.country_registry import registry

__all__ = [
    "APPROVER_ROLES",
    "GATE_MODES",
    "PACK_PURPOSE",
    "SANDBOX_ENVIRONMENTS",
    "Availability",
    "Capability",
    "CountryError",
    "GateDecision",
    "LegalSource",
    "Outcome",
    "PackError",
    "PackKey",
    "PackStatus",
    "PolicyPack",
    "Resolution",
    "Rules",
    "SourceStatus",
    "assurance",
    "availability",
    "claim_matches",
    "fresh_reproof",
    "gate_decision",
    "pack_hash",
    "pack_needed",
    "person_assurance_current",
    "policy_packs",
    "reconcile_delayed",
    "reconstruct_decision",
    "record_presentation",
    "registry",
    "require",
    "revoke",
    "seed_representative_proofing",
    "shared_assertions",
]


async def person_assurance_current(conn: AsyncConnection, principal_id: str, now: datetime) -> bool:
    """Does this person hold at least one valid, unexpired identity verification assertion? Only the result of an identity check counts:
    a registry match, a declaration or a payment never does."""
    from zid_platform.modules.verification.infrastructure.verification_store import assertions_for

    return any(
        a["status"] == "VALID" and a["valid_until"] is not None and a["valid_until"] > now
        for a in await assertions_for(conn, principal_id)
    )
