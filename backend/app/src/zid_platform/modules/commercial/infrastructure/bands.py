"""Load the capability bands and the meter register (`backend/config/entitlements`; ZID_PLATFORM_CONFIG overrides the config root).
Versioned configuration, validated on load: a matrix that breaks the record's invariants does not start."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from zid_platform.modules.commercial.domain.bands import (
    BANDS,
    BandError,
    BandMatrix,
    Cell,
    Feature,
    build,
)

CONFIG = Path(
    os.environ.get("ZID_PLATFORM_CONFIG") or Path(__file__).resolve().parents[6] / "config"
)


def _yaml(name: str) -> dict[str, Any]:
    doc: dict[str, Any] = (
        yaml.safe_load((CONFIG / "entitlements" / name).read_text(encoding="utf-8")) or {}
    )
    return doc


def parse(doc: dict[str, Any]) -> BandMatrix:
    features: list[Feature] = []
    for f in doc.get("features") or []:
        try:
            cells = {str(b): Cell(str(c)) for b, c in (f.get("bands") or {}).items()}
        except ValueError as e:
            raise BandError(f"{f.get('id')}: unknown band cell") from e
        features.append(
            Feature(
                str(f["id"]),
                str(f["section"]),
                str(f["feature"]),
                cells,
                str(f.get("note", "")),
                f.get("catalogue"),
            )
        )
    return build(
        str(doc.get("version", "")),
        {str(b): str(p) for b, p in (doc.get("bands") or {}).items()},
        str(doc.get("band_matrix_enforcement", "REPORT_ONLY")),
        features,
        int(doc.get("snapshot_ttl_seconds", 60)),
    )


@lru_cache(maxsize=1)
def band_matrix() -> BandMatrix:
    return parse(_yaml("capability-bands.yaml"))


@dataclass(frozen=True, slots=True)
class MeterDef:
    key: str
    example_unit: str
    counts: str
    guardrail: str
    existing_meter: str | None
    existing_sku: str | None
    hard_quota: bool
    priced: bool
    bill_on_commit: bool = False


def parse_meters(doc: dict[str, Any]) -> dict[str, MeterDef]:
    out: dict[str, MeterDef] = {}
    for key, body in (doc.get("meters") or {}).items():
        for needed in ("example_unit", "counts", "guardrail"):
            if not str(body.get(needed, "")).strip():
                raise BandError(f"{key}: a meter without a {needed} is not defined")
        out[str(key)] = MeterDef(
            str(key),
            str(body["example_unit"]),
            str(body["counts"]),
            str(body["guardrail"]),
            body.get("existing_meter"),
            body.get("existing_sku"),
            bool(body.get("hard_quota", False)),
            bool(body.get("priced", False)),
            bool(body.get("bill_on_commit", False)),
        )
    if not out:
        raise BandError("an empty meter register")
    return out


@lru_cache(maxsize=1)
def meter_register() -> dict[str, MeterDef]:
    return parse_meters(_yaml("meter-register.yaml"))


__all__ = ["BANDS", "MeterDef", "band_matrix", "meter_register", "parse", "parse_meters"]
