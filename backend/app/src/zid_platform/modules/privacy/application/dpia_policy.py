"""The processing policy a DPIA package was approved against (ZID-DTV-WF-04 QA-004; CR-113).

A DPIA assesses a processing policy: the purpose as registered (its version, data categories, prohibited uses and retention code), the ACTIVE
retention policies that govern the purpose's biometric asset classes, and the provider the package names (its revision, status, locations and
transfer mechanism). The approval stores the digest of that policy (`dpia_package.policy_hash`). When any of it changes afterwards, the release gate
names DPIA_POLICY_CHANGED and the biometric route stops until a new package version is approved against the policy in force.

Read inside the caller's tenant transaction (every row is tenant data). Codes, versions and numbers only.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

POLICY_CHANGED = "DPIA_POLICY_CHANGED"


def digest(body: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


async def policy_body(
    conn: AsyncConnection,
    *,
    purpose_code: str,
    provider_service_ref: str | None,
    object_types: list[str],
) -> dict[str, Any]:
    purpose = (
        await conn.execute(
            text(
                "SELECT purpose_code, purpose_version, allowed_data_categories, prohibited_uses, retention_policy_code FROM privacy.purpose "
                "WHERE purpose_code = :p AND status = 'ACTIVE'"
            ),
            {"p": purpose_code},
        )
    ).first()
    retention = (
        await conn.execute(
            text(
                "SELECT policy_code, version, object_type, retention_days, action_on_expiry, backup_erasure_window_days, legally_required "
                "FROM privacy.retention_policy WHERE status = 'ACTIVE' AND (purpose_code IS NULL OR purpose_code = :p) "
                "AND (object_type = ANY(:t) OR policy_code = :rc) ORDER BY policy_code, version"
            ),
            {
                "p": purpose_code,
                "t": object_types,
                "rc": purpose.retention_policy_code if purpose else None,
            },
        )
    ).all()
    provider = None
    if provider_service_ref:
        provider = (
            await conn.execute(
                text(
                    "SELECT processor_id, revision, status, locations, transfer_mechanism, deletion_sla_days FROM privacy.processor "
                    "WHERE processor_id = :p"
                ),
                {"p": provider_service_ref},
            )
        ).first()
    return {
        "purpose": [
            purpose.purpose_code,
            purpose.purpose_version,
            sorted(purpose.allowed_data_categories),
            sorted(purpose.prohibited_uses),
            purpose.retention_policy_code,
        ]
        if purpose
        else None,
        "retention": [list(r) for r in retention],
        "provider": [
            provider.processor_id,
            provider.revision,
            provider.status,
            sorted(provider.locations),
            provider.transfer_mechanism,
            provider.deletion_sla_days,
        ]
        if provider
        else ([provider_service_ref, "UNREGISTERED"] if provider_service_ref else None),
    }


async def policy_hash(
    conn: AsyncConnection,
    *,
    purpose_code: str,
    provider_service_ref: str | None,
    object_types: list[str],
) -> str:
    return digest(
        await policy_body(
            conn,
            purpose_code=purpose_code,
            provider_service_ref=provider_service_ref,
            object_types=object_types,
        )
    )


def changed(state: str, stored: str | None, current: str | None) -> bool:
    """Pure: an APPROVED package whose stored policy digest is not the policy in force. A package approved before the digest existed has none and
    is not called changed (it keeps its approval until it expires or is superseded)."""
    return state == "APPROVED" and stored is not None and current is not None and stored != current


__all__ = ["POLICY_CHANGED", "changed", "digest", "policy_body", "policy_hash"]
