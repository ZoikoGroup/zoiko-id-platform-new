"""Public Protocol interface of the verification module. Other modules import ONLY this."""

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.verification.application import (
    market_releases,
    policy_packs,
    regulated_claims,
    right_to_rent,
    suspensions,
)
from zid_platform.modules.verification.application.baseline import seed_representative_proofing
from zid_platform.modules.verification.application.entitlements import EntitlementNotFound
from zid_platform.modules.verification.application.entitlements import (
    change_status as change_entitlement_status,
)
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
from zid_platform.modules.verification.domain import attestation as attestation_model
from zid_platform.modules.verification.domain import conformance as conformance_model
from zid_platform.modules.verification.domain import entitlement as entitlement_model
from zid_platform.modules.verification.domain import market_release as release_model
from zid_platform.modules.verification.domain import privacy_overlay as privacy_overlay_model
from zid_platform.modules.verification.domain import regulated_claims as claim_model
from zid_platform.modules.verification.domain import right_to_rent as rtr_model
from zid_platform.modules.verification.domain import suspension as suspension_model
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
from zid_platform.modules.verification.domain.policy_packs import (
    APPROVER_ROLES,
    GATE_MODES,
    PACK_PURPOSE,
    GateDecision,
    LegalSource,
    Outcome,
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

# `PackError` is the service pack registry's (service_packs); the policy pack lifecycle's own error keeps its own name.
from zid_platform.modules.verification.domain.policy_packs import PackError as PolicyPackError
from zid_platform.modules.verification.domain.product_lines import (
    LineRegistry,
    LineState,
    ProductLine,
    ProductLineError,
    require_available,
)
from zid_platform.modules.verification.domain.provider_adapter import (
    Entitlement,
    EntitlementStatus,
    Refusal,
    RouteRequest,
    WebhookRefusal,
    entitlement_refusals,
    route,
    sign_webhook,
    verify_webhook,
)
from zid_platform.modules.verification.domain.service_packs import (
    PackError,
    PackRegistry,
    ServicePack,
    public_view,
    resolve,
)
from zid_platform.modules.verification.infrastructure import attestation_store as attestations
from zid_platform.modules.verification.infrastructure import conformance_store as conformance
from zid_platform.modules.verification.infrastructure import (
    contract_entitlement_store as entitlements,
)
from zid_platform.modules.verification.infrastructure.certification_registry import (
    certification_registry,
)
from zid_platform.modules.verification.infrastructure.claim_templates import claim_templates
from zid_platform.modules.verification.infrastructure.conformance_matrix import conformance_matrix
from zid_platform.modules.verification.infrastructure.country_registry import registry
from zid_platform.modules.verification.infrastructure.pack_registry import pack_registry
from zid_platform.modules.verification.infrastructure.privacy_overlays import privacy_overlays
from zid_platform.modules.verification.infrastructure.product_line_registry import (
    product_line_registry,
)

__all__ = [
    "APPROVER_ROLES",
    "GATE_MODES",
    "PACK_PURPOSE",
    "SANDBOX_ENVIRONMENTS",
    "Availability",
    "Capability",
    "CertClaim",
    "CertRegistry",
    "CertificationError",
    "CountryError",
    "Entitlement",
    "EntitlementNotFound",
    "EntitlementStatus",
    "GateDecision",
    "LegalSource",
    "LineRegistry",
    "LineState",
    "Outcome",
    "PackError",
    "PackKey",
    "PackRegistry",
    "PackStatus",
    "PolicyPack",
    "PolicyPackError",
    "ProductLine",
    "ProductLineError",
    "Refusal",
    "Resolution",
    "RouteRequest",
    "Rules",
    "ServicePack",
    "SourceStatus",
    "WebhookRefusal",
    "assurance",
    "attestation_model",
    "attestations",
    "availability",
    "certification_registry",
    "change_entitlement_status",
    "claim_matches",
    "claim_model",
    "claim_templates",
    "conformance",
    "conformance_matrix",
    "conformance_model",
    "entitlement_model",
    "entitlement_refusals",
    "entitlements",
    "fresh_reproof",
    "gate_decision",
    "market_releases",
    "pack_hash",
    "pack_needed",
    "pack_registry",
    "person_assurance_current",
    "policy_packs",
    "privacy_overlay_model",
    "privacy_overlays",
    "product_line_registry",
    "public_view",
    "reconcile_delayed",
    "reconstruct_decision",
    "record_presentation",
    "refuse_unbacked_claim",
    "registry",
    "regulated_claims",
    "release_model",
    "require",
    "require_available",
    "resolve",
    "revoke",
    "right_to_rent",
    "route",
    "rtr_model",
    "seed_representative_proofing",
    "shared_assertions",
    "sign_webhook",
    "suspension_model",
    "suspensions",
    "verification_session_view",
    "verify_webhook",
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
