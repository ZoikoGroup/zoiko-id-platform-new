"""The country privacy overlay at a verification start (ZID-RCG-003 RCG-G09; T31, T32; CR-079).

Asked before any provider is called, inside the caller's tenant transaction: the country's approved overlay, the provider the country registry names,
whether this profile uses biometrics there, and (only when the country requires it) whether the person gave biometric consent, read through the
privacy module's own check, which denies by default. Returns the decision and, when only the biometric route is the obstacle, the tenant's compliant
alternate profiles (T31).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.privacy.ports import check_processing
from zid_platform.modules.verification.domain import privacy_overlay as po
from zid_platform.modules.verification.domain.verification import VerificationError
from zid_platform.modules.verification.infrastructure import verification_store as store

# the privacy purpose a person consents to for biometric identity verification (registered by the tenant like any other purpose)
BIOMETRIC_PURPOSE = "BIOMETRIC_IDENTITY_VERIFICATION"


async def check_start(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    profile_id: str,
    subject_principal_id: str,
    country: str,
    provider: str | None,
    overlays: Mapping[str, po.Overlay],
) -> tuple[po.RouteDecision, list[Mapping[str, Any]]]:
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
    alts = (
        po.alternates(d, await store.list_profile_routes(conn), country=country.upper())
        if not d.allowed
        else []
    )
    return d, alts
