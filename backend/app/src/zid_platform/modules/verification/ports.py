"""Public Protocol interface of the verification module. Other modules import ONLY this."""

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.verification.application import (
    biometric_eligibility,
    biometric_markets,
    biometric_methods,
    check_cases,
    check_methods,
    coverage,
    market_releases,
    policy_packs,
    register_refresh,
    regulated_claims,
    right_to_rent,
    suspensions,
)
from zid_platform.modules.verification.application.applicant_flow import (
    configure_usage_sink as configure_idv_usage_sink,
)
from zid_platform.modules.verification.application.applicant_flow import idv_coverage
from zid_platform.modules.verification.application.baseline import seed_representative_proofing
from zid_platform.modules.verification.application.entitlements import EntitlementNotFound
from zid_platform.modules.verification.application.entitlements import (
    change_status as change_entitlement_status,
)
from zid_platform.modules.verification.application.identity_signal_feed import (
    feed_pass as identity_feed_pass,
)
from zid_platform.modules.verification.application.policy_review import reevaluate_pending
from zid_platform.modules.verification.application.reverification import (
    sweep as reverification_sweep,
)
from zid_platform.modules.verification.application.service import (
    assurance,
    claim_matches,
    fresh_reproof,
    invalidate_for_decision,
    reconcile_delayed,
    reconstruct_decision,
    record_presentation,
    revoke,
    shared_assertions,
)
from zid_platform.modules.verification.domain import attestation as attestation_model
from zid_platform.modules.verification.domain import biometric_methods as biometric_method_model
from zid_platform.modules.verification.domain import check_methods as check_method_model
from zid_platform.modules.verification.domain import conformance as conformance_model
from zid_platform.modules.verification.domain import entitlement as entitlement_model
from zid_platform.modules.verification.domain import face_calibration as face_calibration
from zid_platform.modules.verification.domain import market_release as release_model
from zid_platform.modules.verification.domain import offerings as offerings_model
from zid_platform.modules.verification.domain import privacy_overlay as privacy_overlay_model
from zid_platform.modules.verification.domain import register_sources as register_sources_model
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
    OPENABLE,
    SANDBOX_ENVIRONMENTS,
    Availability,
    Capability,
    CountryError,
    Opening,
    OpeningKey,
    apply_openings,
    availability,
    check_opening,
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
from zid_platform.modules.verification.infrastructure import (
    country_opening_store,
    face_lab,
    register_sources,
)
from zid_platform.modules.verification.infrastructure.certification_registry import (
    certification_registry,
)
from zid_platform.modules.verification.infrastructure.claim_templates import claim_templates
from zid_platform.modules.verification.infrastructure.conformance_matrix import conformance_matrix
from zid_platform.modules.verification.infrastructure.country_registry import (
    file_registry,
    openings,
    registry,
    set_openings,
)
from zid_platform.modules.verification.infrastructure.image_inspect import (
    ImagingUnavailable,
    read_page_text,
)
from zid_platform.modules.verification.infrastructure.offerings_catalog import offerings_catalog
from zid_platform.modules.verification.infrastructure.pack_registry import pack_registry
from zid_platform.modules.verification.infrastructure.privacy_overlays import privacy_overlays
from zid_platform.modules.verification.infrastructure.product_line_registry import (
    product_line_registry,
)

__all__ = [
    "APPROVER_ROLES",
    "GATE_MODES",
    "OPENABLE",
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
    "ImagingUnavailable",
    "LegalSource",
    "LineRegistry",
    "LineState",
    "Opening",
    "OpeningKey",
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
    "apply_openings",
    "assurance",
    "attestation_model",
    "attestations",
    "availability",
    "biometric_eligibility",
    "biometric_markets",
    "biometric_method_model",
    "biometric_methods",
    "certification_registry",
    "change_entitlement_status",
    "check_cases",
    "check_method_model",
    "check_methods",
    "check_opening",
    "claim_matches",
    "claim_model",
    "claim_templates",
    "configure_idv_usage_sink",
    "conformance",
    "conformance_matrix",
    "conformance_model",
    "country_opening_store",
    "coverage",
    "current_identity_claim",
    "entitlement_model",
    "entitlement_refusals",
    "entitlements",
    "face_calibration",
    "face_lab",
    "file_registry",
    "fresh_reproof",
    "gate_decision",
    "identity_feed_pass",
    "idv_coverage",
    "invalidate_for_decision",
    "market_releases",
    "offerings_catalog",
    "offerings_model",
    "openings",
    "pack_hash",
    "pack_needed",
    "pack_registry",
    "person_assurance_current",
    "policy_packs",
    "privacy_overlay_model",
    "privacy_overlays",
    "product_line_registry",
    "public_view",
    "read_page_text",
    "reconcile_delayed",
    "reconstruct_decision",
    "record_presentation",
    "reevaluate_pending",
    "refuse_unbacked_claim",
    "register_refresh",
    "register_sources",
    "register_sources_model",
    "registry",
    "regulated_claims",
    "release_model",
    "require",
    "require_available",
    "resolve",
    "reverification_sweep",
    "revoke",
    "right_to_rent",
    "route",
    "rtr_model",
    "seed_representative_proofing",
    "set_openings",
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
    from zid_platform.modules.verification.application import idv_claims
    from zid_platform.modules.verification.infrastructure.verification_store import assertions_for

    if any(
        a["status"] == "VALID" and a["valid_until"] is not None and a["valid_until"] > now
        for a in await assertions_for(conn, principal_id)
    ):
        return True
    return (
        await idv_claims.current(conn, principal_id, now) is not None
    )  # an approved proofing case counts too (CR-125)


async def current_identity_claim(
    conn: AsyncConnection, principal_id: str, now: datetime
) -> tuple[str, datetime] | None:
    """The claim type and the time of this person's newest valid, unexpired identity assertion, or None. A screening takes the identity it rests on from here, never from the caller."""
    from zid_platform.modules.verification.application import idv_claims
    from zid_platform.modules.verification.infrastructure.verification_store import assertions_for

    best: tuple[str, datetime] | None = None
    for a in await assertions_for(conn, principal_id):
        if a["status"] == "VALID" and a["valid_until"] is not None and a["valid_until"] > now:
            best = (str(a["claim_type"]), a["performed_at"])
            break
    from_case = await idv_claims.current(
        conn, principal_id, now
    )  # an approved proofing case (CR-125)
    if from_case is not None and (best is None or from_case[1] > best[1]):
        return from_case
    return best


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
