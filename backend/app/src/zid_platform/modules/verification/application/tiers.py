"""The person's verification tier from what is on record (ZID-EDR-006). Reads facts; decides nothing about who the person is."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection
from zid_db import in_tenant

from zid_platform.modules.verification.application import service
from zid_platform.modules.verification.domain import tiers as t
from zid_platform.modules.verification.infrastructure import verification_store as store


async def email_proved(conn: AsyncConnection, principal_id: str, registration_tenant: str | None) -> bool:
    """Did this person prove control of an inbox at sign-up? The sign-up record is the only proof kept; an account made another way has none on
    record, and says so rather than being assumed."""
    if not registration_tenant:
        return False
    async with in_tenant(conn, registration_tenant):
        row = (
            await conn.execute(
                text("SELECT 1 FROM platform.registration_account WHERE account_principal_id = :p LIMIT 1"),
                {"p": principal_id},
            )
        ).first()
    return row is not None


async def facts_for(
    conn: AsyncConnection,
    *,
    principal_id: str,
    authn_level: int,
    registration_tenant: str | None,
    organization_state: str | None,
    now: datetime,
) -> t.Facts:
    held = await service.assurance(conn, principal_id, now)
    ial = int(str(held["identity_proofing"]["level"]).rsplit("Z", 1)[-1])
    org_verified = organization_state == "VERIFIED"  # a verified organization has its representative's authority confirmed by a reviewer (CR-052)
    return t.Facts(
        email_proved=await email_proved(conn, principal_id, registration_tenant),
        proofed=ial >= 2,  # IAL-Z2 is the profile that needs a binding and liveness (or an accepted equivalent)
        company_verified=org_verified,
        mandate_evidence=org_verified,
        strong_authentication=authn_level >= 2,
        fresh_strong_sign_in=authn_level >= 3,
        independent_approval=False,  # a fact about one action, never about a person
    )


async def tier_view(
    conn: AsyncConnection,
    policy: t.TierPolicy,
    facts: t.Facts,
    *,
    principal_id: str,
    now: datetime,
    needs: set[str] | None = None,
) -> dict[str, Any]:
    result = t.tier_of(facts, policy)
    rows = await store.assertions_for(conn, principal_id)
    return {
        "policy_version": policy.version,
        "tier": result.tier.value,
        "label": result.label,
        "meaning": result.meaning,
        "next_tier": result.next_tier.value if result.next_tier else None,
        "missing": [{"check": c, "why": f"NEEDED_FOR_{result.next_tier.value}"} for c in result.missing] if result.next_tier else [],
        "reasons": list(result.reasons),
        "facts": {
            "email_proved": facts.email_proved,
            "identity_proofed": facts.proofed,
            "company_verified": facts.company_verified,
            "strong_authentication": facts.strong_authentication,
            "fresh_strong_sign_in": facts.fresh_strong_sign_in,
        },
        "claims": [t.claim_view(r, policy, now) for r in rows],
        "alternatives": [
            {"id": a["id"], "for": list(a.get("for") or []), "effect": a["effect"], "status": a["status"]} for a in t.alternatives_for(needs or set(), policy)
        ],
        "never_identity_proof": sorted(policy.never_identity_proof),
        "note": "Not identity verified means exactly that. A phone code, an email confirmation, single sign-on, a selfie alone or control of a domain is never proof of who a person is.",
    }
