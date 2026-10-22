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

from zid_platform.modules.operations.domain.catalogue import Catalogue, CatalogueError
from zid_platform.modules.operations.infrastructure.store import CATALOGUE_FILE, catalogue


def service_catalogue(path: str | None = None) -> Catalogue:
    return catalogue(Path(path) if path else CATALOGUE_FILE)


def catalogue_journeys(cat: Catalogue) -> list[tuple[str, str, float]]:
    return cat.journeys()


__all__ = ["Catalogue", "CatalogueError", "catalogue_journeys", "service_catalogue"]
