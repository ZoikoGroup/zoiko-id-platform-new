"""Public interface of the operations module. Other modules import ONLY this.

- `service_catalogue(path=None)`: the parsed production service catalogue
  (backend/config/operations/service-catalogue.yaml) - owners, tiers, journeys, SLOs, runbooks; the
  app shell measures each service's journeys from it.
- `catalogue_journeys(catalogue)`: (journey, prefix, latency threshold) triples for the shell.

Incidents, readiness reviews, change freezes and the SLO / change-policy views are operator-plane
endpoints of this module (`/ops/...`).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.operations.domain.catalogue import Catalogue, CatalogueError
from zid_platform.modules.operations.infrastructure.store import CATALOGUE_FILE, catalogue


def service_catalogue(path: str | None = None) -> Catalogue:
    return catalogue(Path(path) if path else CATALOGUE_FILE)


def catalogue_journeys(cat: Catalogue) -> list[tuple[str, str, float]]:
    return cat.journeys()


async def public_incidents(
    conn: AsyncConnection, *, cell_id: str | None, limit: int = 20
) -> list[dict[str, Any]]:
    """What a customer may see of the incident registry (ZID-CPS-WF-02 07.09 item 03): the same incident id the operators hold, its severity,
    status, services and times, and only the COMMUNICATION entries the operators published. A security incident's detail and every internal note
    stay inside; an incident pinned to other cells is not shown."""
    from zid_platform.modules.operations.infrastructure.store import incidents, timeline

    out = []
    for i in await incidents(conn, open_only=False, limit=limit):
        if i.cells and cell_id and cell_id not in i.cells:
            continue
        notes = [
            {"at": e["at"].isoformat(), "note": e["note"]}
            for e in await timeline(conn, i.incident_id)
            if e.get("kind") == "COMMUNICATION"
        ]
        out.append({
            "incident_id": i.incident_id, "severity": i.severity.value, "status": i.status.value, "services": list(i.services),
            "declared_at": i.declared_at.isoformat(), "resolved_at": i.resolved_at.isoformat() if i.resolved_at else None,
            "title": "Security incident" if i.security else i.title, "updates": [] if i.security else notes,
        })  # fmt: skip
    return out


async def incident_summary(conn: AsyncConnection, incident_id: str) -> dict[str, Any] | None:
    """One incident's id, severity, status, services and whether it is a security incident, for a module that publishes about it (the Trust Center's advisories). None if there is no such incident."""
    from zid_platform.modules.operations.infrastructure.store import load_incident

    i = await load_incident(conn, incident_id)
    if i is None:
        return None
    return {
        "incident_id": i.incident_id,
        "severity": i.severity.value,
        "status": i.status.value,
        "services": list(i.services),
        "security": i.security,
    }


__all__ = [
    "Catalogue",
    "CatalogueError",
    "catalogue_journeys",
    "incident_summary",
    "public_incidents",
    "service_catalogue",
]
