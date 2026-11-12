"""Public Protocol interface of the verification module. Other modules import ONLY this."""

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncConnection

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
from zid_platform.modules.verification.domain.certification import (
    CertClaim,
    CertificationError,
    CertRegistry,
    refuse_unbacked_claim,
)
from zid_platform.modules.verification.domain.countries import (
    SANDBOX_ENVIRONMENTS,
    Availability,
    Capability,
    CountryError,
    availability,
    require,
)
from zid_platform.modules.verification.domain.product_lines import (
    LineRegistry,
    LineState,
    ProductLine,
    ProductLineError,
    require_available,
)
from zid_platform.modules.verification.domain.service_packs import (
    PackError,
    PackRegistry,
    ServicePack,
    public_view,
    resolve,
)
from zid_platform.modules.verification.infrastructure.certification_registry import (
    certification_registry,
)
from zid_platform.modules.verification.infrastructure.country_registry import registry
from zid_platform.modules.verification.infrastructure.pack_registry import pack_registry
from zid_platform.modules.verification.infrastructure.product_line_registry import (
    product_line_registry,
)

__all__ = [
    "SANDBOX_ENVIRONMENTS",
    "Availability",
    "Capability",
    "CertClaim",
    "CertRegistry",
    "CertificationError",
    "CountryError",
    "LineRegistry",
    "LineState",
    "PackError",
    "PackRegistry",
    "ProductLine",
    "ProductLineError",
    "ServicePack",
    "assurance",
    "availability",
    "certification_registry",
    "claim_matches",
    "fresh_reproof",
    "pack_registry",
    "person_assurance_current",
    "product_line_registry",
    "public_view",
    "reconcile_delayed",
    "reconstruct_decision",
    "record_presentation",
    "refuse_unbacked_claim",
    "registry",
    "require",
    "require_available",
    "resolve",
    "revoke",
    "seed_representative_proofing",
    "shared_assertions",
    "verification_session_view",
]


async def person_assurance_current(conn: AsyncConnection, principal_id: str, now: datetime) -> bool:
    """Does this person hold at least one valid, unexpired identity verification assertion? Only the result of an identity check counts:
    a registry match, a declaration or a payment never does."""
    from zid_platform.modules.verification.infrastructure.verification_store import assertions_for

    return any(
        a["status"] == "VALID" and a["valid_until"] is not None and a["valid_until"] > now
        for a in await assertions_for(conn, principal_id)
    )


async def verification_session_view(
    conn: AsyncConnection, verification_id: str
) -> dict[str, str] | None:
    """The status, outcome code and subject of one identity verification session, or None if there is none. Other modules (the
    verification case) read an identity result only through this."""
    from zid_platform.modules.verification.domain.verification import VerificationError
    from zid_platform.modules.verification.infrastructure import verification_store

    try:
        s = await verification_store.load_session(conn, verification_id)
    except VerificationError:
        return None
    return {
        "status": s.status.value,
        "outcome_code": s.outcome_code or "",
        "subject_principal_id": s.subject_principal_id,
    }
