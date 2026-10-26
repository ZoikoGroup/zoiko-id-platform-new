"""The platform baseline profile for proofing a company's representative (ZID-VERIFY-001 §2-3,
§19; ZID-ROOT-001 §33 handoff; ZID-OVERLAY-001 §4).

A newly registered company needs one way to prove its registrant's identity before anyone in it can
administer profiles (the registrant is not yet an owner). As with the policy baseline, the platform
seeds an approved, versioned profile at sign-up:
- subject: the human representative; target IAL-Z2, the spec's "default protected enterprise
  onboarding floor", so person-to-evidence binding is required;
- required claims legal_name (what the organization gate matches against the register's officers)
  and birthdate; evidence: government ID or a digital identity credential;
- manual review allowed (fraud never auto-passes), one reviewer; reusable for onboarding and
  representative proofing only, for a year.
The company can later add or replace profiles through the governed profile API.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection
from zid_outbox import emit

from zid_platform.modules.verification.domain.verification import validate_requirements
from zid_platform.modules.verification.infrastructure import verification_store as store
from zid_platform.modules.verification.infrastructure.simulator import (
    SIM_ADAPTER_ID,
    SIM_EVIDENCE,
    SIM_GROUP,
    SIM_MAPPING_VERSION,
    SIM_PROVIDER,
    SIM_SUBMIT_URL,
)

PROFILE_NAME = "representative-proofing"
PROFILE_LEVEL = "IAL-Z2"


def requirements(jurisdictions: list[str]) -> dict[str, Any]:
    return {
        "subject_type": "REPRESENTATIVE",
        "required_claims": ["legal_name", "birthdate"],
        "accepted_evidence": ["GOVERNMENT_ID", "DIGITAL_CREDENTIAL"],
        "binding_required": True,
        "liveness_required": True,
        "biometric_jurisdictions": list(jurisdictions),
        "jurisdictions": list(jurisdictions),
        "freshness_days": 365,
        "manual_review": True,
        "review_approvals": 1,
        "reliance_purposes": ["representative_proofing", "onboarding"],
    }


async def seed_representative_proofing(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    now: datetime,
    jurisdictions: list[str],
    simulator_jwks: dict[str, Any] | None = None,
) -> str | None:
    """`conn` is scoped to the tenant. Returns the new ACTIVE profile id, or None if the tenant
    already has one. With `simulator_jwks` (local/test), also registers the development provider."""
    created: str | None = None
    existing = [
        p
        for p in await store.list_profiles(conn)
        if p.get("name") == PROFILE_NAME and p.get("status") == "ACTIVE"
    ]
    if not existing:
        req = requirements(jurisdictions)
        validate_requirements(PROFILE_LEVEL, req)
        pid, version = await store.create_profile(
            conn, tenant_id=tenant_id, name=PROFILE_NAME, level=PROFILE_LEVEL, requirements=req
        )
        await store.activate_profile(conn, await store.load_profile(conn, pid), now)
        await emit(
            conn,
            name="verification.profile.activated",
            tenant_id=tenant_id,
            aggregate_type="verification_profile",
            aggregate_id=pid,
            aggregate_version=1,
            data={
                "profile_id": pid,
                "name": PROFILE_NAME,
                "version": version,
                "origin": "PLATFORM_BASELINE",
            },
        )
        created = pid
    if simulator_jwks is not None:
        await ensure_simulator_adapter(conn, tenant_id, simulator_jwks, jurisdictions)
    return created


async def ensure_simulator_adapter(
    conn: AsyncConnection, tenant_id: str, jwks: dict[str, Any], jurisdictions: list[str]
) -> None:
    """Register the development provider, or re-pin its current key (local/test only)."""
    if await store.load_adapter(conn, SIM_ADAPTER_ID) is None:
        await store.create_adapter(
            conn,
            {
                "adapter_id": SIM_ADAPTER_ID,
                "tenant_id": tenant_id,
                "provider": SIM_PROVIDER,
                "mapping_version": SIM_MAPPING_VERSION,
                "submit_endpoint": SIM_SUBMIT_URL,
                "callback_jwks": jwks,
                "callback_algorithms": ["ES256"],
                "equivalence_group": SIM_GROUP,
                "supported_evidence": list(SIM_EVIDENCE),
                "jurisdictions": list(jurisdictions),
            },
        )
        return
    await conn.execute(
        text(
            "UPDATE verify.provider_adapter SET callback_jwks = CAST(:j AS jsonb), status = 'ACTIVE', "
            "consecutive_failures = 0, open_until = NULL WHERE adapter_id = :a"
        ),
        {"j": json.dumps(jwks), "a": SIM_ADAPTER_ID},
    )
