"""The country privacy overlay at a verification start (ZID-RCG-003 RCG-G09; T31, T32; CR-079).

Asked before any provider is called, inside the caller's tenant transaction: the country's approved overlay, the provider the country registry names,
whether this profile uses biometrics there, and (only when the country requires it) whether the person gave biometric consent, read through the
privacy module's own check, which denies by default. Returns the decision and, when only the biometric route is the obstacle, the tenant's compliant
alternate profiles (T31).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.privacy.ports import check_processing
from zid_platform.modules.verification.application import biometric_eligibility
from zid_platform.modules.verification.domain import privacy_overlay as po
from zid_platform.modules.verification.domain.biometric_methods import Method
from zid_platform.modules.verification.domain.verification import VerificationError
from zid_platform.modules.verification.infrastructure import verification_store as store

# the privacy purpose a person consents to for biometric identity verification (registered by the tenant like any other purpose)
BIOMETRIC_PURPOSE = "BIOMETRIC_IDENTITY_VERIFICATION"
# a biometric route refused because the provider would process the data outside a home region without a lawful transfer, or in no known region
REGION_REASONS = frozenset({"CROSS_BORDER_ROUTE_DENIED", "PROVIDER_REGION_UNKNOWN"})


async def record_region_blocked(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    country: str,
    provider: str | None,
    overlay: po.Overlay | None,
    decision: po.RouteDecision,
) -> None:
    """WF-04 QA-029 (CR-113): a biometric route that would have left the country's approved regions failed closed. Its own event, so the region
    fallback is visible apart from the other gates: the country, the provider, the region the provider names (or none), the reason codes and the
    overlay hash. Never a person, a session, an image or a score."""
    import uuid

    from zid_outbox import emit

    await emit(
        conn,
        name="verification.biometric.region_blocked",
        tenant_id=tenant_id,
        aggregate_type="biometric_region_refusal",
        aggregate_id=str(uuid.uuid4()),
        aggregate_version=1,
        data={
            "country": country.upper(),
            "provider": provider,
            "provider_region": overlay.provider_regions.get(provider or "") if overlay else None,
            "reasons": sorted(REGION_REASONS.intersection(decision.reasons)),
            "overlay_hash": po.overlay_hash(overlay) if overlay is not None else None,
        },
    )


async def check_start(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    profile_id: str,
    subject_principal_id: str,
    country: str,
    provider: str | None,
    overlays: Mapping[str, po.Overlay],
    purpose: str = "IDENTITY_VERIFICATION",
    self_run: bool = False,
    now: datetime | None = None,
) -> tuple[po.RouteDecision, list[Mapping[str, Any]]]:
    """CR-110: when the profile uses biometrics in this country through a PROVIDER, face match and liveness must both be eligible for this tenant
    (`biometric_eligibility` with the provider as the engine: method state, DPIA, overlay, breach pause, retention, evaluation of the pinned model).
    A self-run start skips this: the self-run engine is checked when the chip and the face arrive."""
    overlay = overlays.get(country.upper())
    try:
        profile = await store.load_profile(conn, profile_id)
        biometric = country.upper() in profile.biometric_jurisdictions
    except (
        VerificationError
    ):  # an unknown profile is refused by the start itself; the route is still checked
        biometric = False
    consent: bool | None = None
    if biometric and overlay is not None and overlay.biometrics is po.Biometrics.CONSENT_REQUIRED:
        decision = await check_processing(
            conn,
            tenant_id,
            activity_code="IDENTITY_VERIFICATION",
            purpose_code=BIOMETRIC_PURPOSE,
            data_category="D3",
            subject_principal_id=subject_principal_id,
        )
        consent = decision.allowed
    d = po.route(overlay, provider=provider, biometric=biometric, consent=consent)
    if biometric and tenant_id and REGION_REASONS.intersection(d.reasons):
        await record_region_blocked(
            conn, tenant_id, country=country, provider=provider, overlay=overlay, decision=d
        )
    if biometric and provider and not self_run:
        checked = await biometric_eligibility.eligibility(
            conn,
            tenant_id=tenant_id,
            country=country,
            purpose=purpose.upper(),
            overlays=overlays,
            now=now or datetime.now(UTC),
            provider=provider,
            rollout_key=subject_principal_id,  # CR-110: a partial rollout picks people, never sessions
        )
        if not all(checked.available(m) for m in Method):
            d = replace(d, allowed=False, reasons=(*d.reasons, "BIOMETRIC_METHOD_NOT_ELIGIBLE"))
            await biometric_eligibility.record_blocked(
                conn, tenant_id, checked, route=f"provider:{provider}"
            )
    alts = (
        po.alternates(d, await store.list_profile_routes(conn), country=country.upper())
        if not d.allowed
        else []
    )
    return d, alts
