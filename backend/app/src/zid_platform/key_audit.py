"""The software key store's audit trail, copied into the evidence ledger (CR-060).

Every create, rotate, retire, destroy and re-wrap of a key is written to `keys.key_event` in the same transaction as the change. Those rows
live next to the keys, so on their own they could be rewritten by whoever can rewrite the keys; this step copies each into the
platform-security partition of the ledger (class E3), where it is hash-chained, signed in checkpoints and externally timestamped like every other
administrative event, and stamps the row `ledgered_at`. It is idempotent: a copy is keyed by the event id, so a repeat is skipped.

It lives at the platform level rather than in a module because the key store belongs to no tenant-facing module.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.evidence.ports import AuditFacts, EvidenceClass, record_audit

_SELECT = (
    "SELECT event_id, key_id, version, action, purpose, algorithm, actor, occurred_at FROM keys.key_event "
    "WHERE ledgered_at IS NULL ORDER BY occurred_at, event_id LIMIT :n"
)
_MARK = "UPDATE keys.key_event SET ledgered_at = clock_timestamp() WHERE event_id = :id AND ledgered_at IS NULL"
_PROCESS = re.compile(r"[^a-z0-9_-]")


def audit_facts(
    *,
    event_id: str,
    key_id: str,
    version: int,
    action: str,
    purpose: str,
    algorithm: str,
    actor: str,
    occurred_at: datetime,
) -> AuditFacts:
    """What the ledger records for one key event. Ids and codes only: an operator is a pseudonymous reference, never a name or address."""
    kind, _, rest = actor.partition(":")
    details: dict[str, str | int] = {
        "key_id": key_id[:200],
        "version": version,
        "purpose": purpose,
        "algorithm": algorithm,
    }
    if kind == "cli":
        actor_type = "OPERATOR"
        details["operator_ref"] = hashlib.sha256(actor.encode()).hexdigest()[:16]
    else:
        actor_type = "SYSTEM"
        details["process"] = _PROCESS.sub("", rest.lower())[:40] or "unknown"
    return AuditFacts(
        event_type=f"key.{action.lower()}",
        actor_type=actor_type,
        action=action.lower(),
        target_type="software_key",
        target_ref=f"{key_id}:v{version}"[:200],
        outcome="SUCCESS",
        evidence_class=EvidenceClass.E3,
        occurred_at=occurred_at,
        source_service="zid-keys",
        source_event_id=event_id,
        details=details,
    )


async def ledger_key_events(conn: AsyncConnection, *, limit: int = 200) -> int:
    """Copy up to `limit` key events into the ledger, oldest first. Returns how many rows were stamped."""
    rows = (await conn.execute(text(_SELECT), {"n": limit})).all()
    stamped = 0
    for r in rows:
        await record_audit(
            conn,
            None,
            audit_facts(
                event_id=str(r.event_id),
                key_id=r.key_id,
                version=int(r.version),
                action=r.action,
                purpose=r.purpose,
                algorithm=r.algorithm,
                actor=r.actor,
                occurred_at=r.occurred_at,
            ),
        )  # None back means this event id is already in the ledger: stamp it all the same
        await conn.execute(text(_MARK), {"id": r.event_id})
        stamped += 1
    return stamped
