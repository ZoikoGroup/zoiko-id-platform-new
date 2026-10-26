"""Load the cell catalog and residency profiles (`backend/config/cells`, `residency-profiles`;
ZID_PLATFORM_CONFIG overrides the config root). Versioned configuration, not tenant data."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from zid_platform.modules.tenant.domain.residency import Catalog, Cell, Placement

CONFIG = Path(
    os.environ.get("ZID_PLATFORM_CONFIG") or Path(__file__).resolve().parents[6] / "config"
)


def _yaml(path: Path) -> dict[str, Any]:
    doc: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return doc


@lru_cache(maxsize=1)
def catalog() -> Catalog:
    cells_doc = _yaml(CONFIG / "cells" / "cells.yaml")
    profiles_doc = _yaml(CONFIG / "residency-profiles" / "residency-profiles.yaml")
    cells: dict[str, Cell] = {}
    placements: dict[str, dict[str, Placement]] = {}
    for cell_id, body in (cells_doc.get("cells") or {}).items():
        juris = body.get("jurisdiction") or {}
        recovery = body.get("recovery") or {}
        placements[cell_id] = {
            name: Placement(
                region=loc.get("region"),
                country=loc.get("country"),
                dedicated=bool(loc.get("dedicated", False)),
            )
            for name, loc in (body.get("subsystem_placements") or {}).items()
        }
        cells[cell_id] = Cell(
            cell_id=cell_id,
            region=body.get("region"),
            country=juris.get("country"),
            legal_zone=juris.get("legal_zone"),
            supported_profiles=tuple(body.get("supported_profiles") or ()),
            approved_recovery_cells=tuple(recovery.get("approved_recovery_cells") or ()),
            recovery_only=body.get("role") == "recovery_only",
        )
    return Catalog(
        cells=cells,
        profiles=profiles_doc.get("profiles") or {},
        coverage_subsystems=tuple(profiles_doc.get("coverage_matrix_subsystems") or ()),
        placements=placements,
        extra={"cross_border_default": profiles_doc.get("cross_border_default", "DENY")},
    )


__all__ = ["CONFIG", "catalog"]
