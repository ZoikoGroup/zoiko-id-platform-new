"""Record a Travel Rule message (DTV-33; CR-101). Validates first, then keeps what was found, never the message.

Nothing is sent to a partner. Recording never decides to release or hold a transfer: a person does. A payload that is not well formed is
recorded too (its finding codes are the point), so a company can see what it received and why it could not be relied on.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.evidence.ports import AuditFacts, EvidenceClass, record_audit
from zid_platform.modules.verification.domain import travel_rule as tr
from zid_platform.modules.verification.infrastructure import travel_rule_store as store

DIRECTIONS = ("OUTGOING", "INCOMING")


def payload_hash(payload: Mapping[str, Any]) -> str:
    """SHA-256 of the canonical JSON, so the same message gives the same hash whatever the key order."""
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def view(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "message_id": str(row["message_id"]),
        "direction": row["direction"],
        "jurisdiction": row["jurisdiction"],
        "payload_hash": row["payload_hash"],
        "originating_lei": row["originating_lei"],
        "beneficiary_lei": row["beneficiary_lei"],
        "well_formed": row["well_formed"],
        "finding_codes": list(row["finding_codes"]),
        "originating_vasp": row["originating_vasp"],
        "beneficiary_vasp": row["beneficiary_vasp"],
        "threshold": row["threshold"],
        "registry_version": row["registry_version"],
        "recorded_by": str(row["recorded_by"]),
        "created_at": row["created_at"].isoformat(),
    }


async def record(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    actor: str,
    direction: str,
    jurisdiction: str,
    payload: Mapping[str, Any],
    registry: tr.VaspRegistry,
    now: datetime,
) -> tuple[dict[str, Any], bool]:
    if direction not in DIRECTIONS:
        raise tr.TravelRuleError("DIRECTION_INVALID", direction)
    result = tr.assess(payload, registry, jurisdiction=jurisdiction)
    row, created = await store.insert(
        conn,
        tenant_id=tenant_id,
        row={
            "direction": direction,
            "jurisdiction": jurisdiction,
            "payload_hash": payload_hash(payload),
            "originating_lei": result.originating_lei,
            "beneficiary_lei": result.beneficiary_lei,
            "well_formed": result.well_formed,
            "finding_codes": sorted({f.code for f in result.findings}),
            "originating_vasp": result.originating_vasp.value,
            "beneficiary_vasp": result.beneficiary_vasp.value,
            "threshold": result.threshold.value,
            "registry_version": registry.version,
            "recorded_by": actor,
        },
    )
    if created:
        import uuid

        await record_audit(
            conn,
            tenant_id,
            AuditFacts(
                event_type="verification.travel_rule.recorded",
                actor_type="HUMAN",
                action="recorded",
                target_type="travel_rule_message",
                target_ref=str(row["message_id"]),
                outcome="SUCCESS",
                evidence_class=EvidenceClass.E2,
                occurred_at=now,
                actor_principal_id=actor,
                source_service="verification",
                source_event_id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"tr:{row['message_id']}")),
                details={"direction": direction, "well_formed": result.well_formed},
            ),
        )
    return row, created
