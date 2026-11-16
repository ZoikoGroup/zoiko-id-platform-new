"""Record and read ownership graphs (DTV-24; CR-102). The rules are in `domain/ownership.py`; this layer stores a version and works out its findings.

The findings are candidates for a person to confirm and gaps nobody has explained: never a statement that someone IS a beneficial owner. They are not
stored. They are worked out from the recorded version each time, so the same version always answers the same way.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Mapping
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


def graph_view(row: Mapping[str, Any]) -> dict[str, Any]:
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
