"""Record and read ownership graphs (DTV-24; CR-102). The rules are in `domain/ownership.py`; this layer stores a version and works out its findings.

The findings are candidates for a person to confirm and gaps nobody has explained: never a statement that someone IS a beneficial owner. They are not
stored. They are worked out from the recorded version each time, so the same version always answers the same way.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.evidence.ports import AuditFacts, EvidenceClass, record_audit
from zid_platform.modules.organization.domain import ownership as own
from zid_platform.modules.organization.infrastructure import ownership_store as store


def _threshold(value: Any) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, str | int):
        raise own.OwnershipError("THRESHOLD_INVALID", "a decimal string")
    try:
        d = Decimal(str(value))
    except InvalidOperation as e:
        raise own.OwnershipError("THRESHOLD_INVALID", str(value)) from e
    if not d.is_finite() or not Decimal(0) < d <= Decimal(100):
        raise own.OwnershipError("THRESHOLD_INVALID", str(value))
    return d


def findings_view(f: own.Findings) -> dict[str, Any]:
    return {
        "target": f.target,
        "threshold": own.percent_text(f.threshold),
        "candidates": [
            {
                "entity_id": c.entity_id,
                "effective_percent": own.percent_text(c.effective_percent),
                "reasons": list(c.reasons),
                "paths": c.paths,
                "status": c.status,
            }
            for c in f.candidates
        ],
        "gaps": [
            {
                "code": g.code,
                "entity_id": g.entity_id,
                "effective_percent": own.percent_text(g.effective_percent),
            }
            for g in f.gaps
        ],
        "recognised_ends": list(f.recognised_ends),
        "complete": f.complete,
        "recommendation": f.recommendation,
        "fallback": f.fallback,
        "summary": dict(own.summary(f)),
        "notes": list(f.notes),
    }


def analyze(
    entities: list[dict[str, Any]], links: list[dict[str, Any]], target: str, threshold: Any = "25"
) -> dict[str, Any]:
    """Nothing is stored. A graph that cannot be read, loops, or is more than 100 percent owned is refused whole with a code."""
    graph = own.build(entities, links)
    return findings_view(own.analyze(graph, target, threshold=_threshold(threshold)))


def content_hash(
    target: str, entities: list[dict[str, Any]], links: list[dict[str, Any]], threshold: Decimal
) -> str:
    canon = json.dumps(
        {
            "target": target,
            "entities": sorted(entities, key=lambda e: str(e.get("id"))),
            "links": links,
            "threshold": str(threshold),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canon.encode()).hexdigest()


DECISIONS = ("CONFIRMED", "NOT_CONFIRMED", "ESCALATED")


def confirmation_view(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "confirmation_id": str(row["confirmation_id"]),
        "entity_id": row["entity_id"],
        "decision": row["decision"],
        "reason": row["reason"],
        "evidence_ref": row["evidence_ref"],
        "reviewer": str(row["reviewer"]),
        "created_at": row["created_at"].isoformat(),
    }


def graph_view(
    row: Mapping[str, Any], confirmations: Sequence[Mapping[str, Any]] | None = None
) -> dict[str, Any]:
    entities, links = list(row["entities"]), list(row["links"])
    out: dict[str, Any] = {
        "graph_id": str(row["graph_id"]),
        "target": row["target"],
        "version": row["version"],
        "threshold": own.percent_text(Decimal(row["threshold"])),
        "content_hash": row["content_hash"],
        "author": str(row["author"]),
        "created_at": row["created_at"].isoformat(),
        "entities": entities,
        "links": links,
        "confirmations": [confirmation_view(c) for c in (confirmations or [])],
    }
    try:
        out["findings"] = analyze(entities, links, row["target"], str(row["threshold"]))
    except (
        own.OwnershipError
    ) as e:  # a stored graph passed these rules when saved; a rule change must not hide it
        out["findings"] = None
        out["findings_error"] = e.code
    return out


async def save(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    actor: str,
    target: str,
    entities: list[dict[str, Any]],
    links: list[dict[str, Any]],
    threshold: Any,
    now: datetime,
) -> dict[str, Any]:
    """Refuses a graph that does not pass the rules (so a loop or an over-owned company is never stored), then records the next version."""
    th = _threshold(threshold)
    own.analyze(own.build(entities, links), target, threshold=th)
    row = await store.insert(
        conn,
        tenant_id=tenant_id,
        target=target,
        version=await store.next_version(conn, target),
        entities=entities,
        links=links,
        threshold=th,
        content_hash=content_hash(target, entities, links, th),
        author=actor,
    )
    await record_audit(
        conn,
        tenant_id,
        AuditFacts(
            event_type="organization.ownership.recorded",
            actor_type="HUMAN",
            action="recorded",
            target_type="ownership_graph",
            target_ref=str(row["graph_id"]),
            outcome="SUCCESS",
            evidence_class=EvidenceClass.E2,
            occurred_at=now,
            actor_principal_id=actor,
            source_service="organization",
            source_event_id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"own:{row['graph_id']}")),
            details={"version": row["version"], "entities": len(entities), "links": len(links)},
        ),
    )
    return graph_view(row)


async def confirm(
    conn: AsyncConnection,
    tenant_id: str,
    graph_id: str,
    *,
    entity_id: str,
    decision: str,
    reason: str,
    evidence_ref: str,
    reviewer: str,
    now: datetime,
) -> dict[str, Any]:
    """A person decides on one candidate of one recorded version. The platform never decides: this only records the person's decision, with a
    reason and an evidence reference. Refused when the entity is not a candidate of that version, when it already has a decision, or when the reviewer
    recorded the graph."""
    if decision not in DECISIONS:
        raise own.OwnershipError("DECISION_INVALID", decision)
    if not 20 <= len(reason.strip()) <= 2000:
        raise own.OwnershipError("REASON_TOO_SHORT", "at least a sentence")
    if not evidence_ref.strip():
        raise own.OwnershipError("EVIDENCE_REF_REQUIRED")
    row = await store.get(conn, graph_id)
    if row is None:
        raise own.OwnershipError("GRAPH_NOT_FOUND", graph_id)
    if str(row["author"]) == reviewer:
        raise own.OwnershipError("REVIEWER_IS_AUTHOR", "someone else decides the candidates")
    found = analyze(list(row["entities"]), list(row["links"]), row["target"], str(row["threshold"]))
    if entity_id not in {c["entity_id"] for c in found["candidates"]}:
        raise own.OwnershipError("NOT_A_CANDIDATE", entity_id)
    if any(c["entity_id"] == entity_id for c in await store.confirmations_for(conn, graph_id)):
        raise own.OwnershipError("ALREADY_DECIDED", entity_id)
    saved = await store.insert_confirmation(
        conn,
        tenant_id=tenant_id,
        graph_id=graph_id,
        entity_id=entity_id,
        decision=decision,
        reason=reason.strip(),
        evidence_ref=evidence_ref.strip(),
        reviewer=reviewer,
    )
    await record_audit(
        conn,
        tenant_id,
        AuditFacts(
            event_type="organization.ownership.decided",
            actor_type="HUMAN",
            action="decided",
            target_type="ownership_graph",
            target_ref=graph_id,
            outcome="SUCCESS",
            evidence_class=EvidenceClass.E2,
            occurred_at=now,
            actor_principal_id=reviewer,
            source_service="organization",
            source_event_id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"ownd:{saved['confirmation_id']}")),
            details={
                "decision": decision
            },  # a code only: never the reason, the evidence or the entity
        ),
    )
    return confirmation_view(saved)
