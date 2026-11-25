"""Load the country capability registry (`backend/config/country-capabilities`; ZID_PLATFORM_CONFIG overrides
the config root). Versioned configuration: a country is switched on or off by editing the file, never code."""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from zid_platform.modules.verification.domain.countries import (
    Availability,
    Capability,
    CapabilityEntry,
    Country,
    CountryError,
    Opening,
    OpeningKey,
    Registry,
    apply_openings,
    validate,
)

CONFIG = Path(
    os.environ.get("ZID_PLATFORM_CONFIG") or Path(__file__).resolve().parents[6] / "config"
)
FILE = "country-capabilities/country-capabilities.yaml"


def _when(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    return datetime.fromisoformat(str(value)).replace(tzinfo=UTC)


def parse(doc: dict[str, Any]) -> Registry:
    countries: dict[str, Country] = {}
    for code, body in (doc.get("countries") or {}).items():
        caps: dict[Capability, CapabilityEntry] = {}
        for name, raw in (body.get("capabilities") or {}).items():
            try:
                cap = Capability(name)
                state = Availability(raw.get("status", "DISABLED"))
            except ValueError as e:
                raise CountryError("COUNTRY_REGISTRY_INVALID", f"{code}/{name}") from e
            caps[cap] = CapabilityEntry(
                availability=state,
                provider=raw.get("provider"),
                legal_status=raw.get("legal_status"),
                effective_from=_when(raw.get("effective_from")),
                approvals=dict(raw.get("approvals") or {}),
            )
        countries[code] = Country(
            code=code,
            name=body.get("name", code),
            wave=body.get("wave"),
            capabilities=caps,
            contact=body.get("contact"),
        )
    registry = Registry(version=str(doc.get("version", "0")), countries=countries)
    validate(registry)
    return registry


@lru_cache(maxsize=1)
def file_registry() -> Registry:
    """The registry exactly as the file says it."""
    doc: dict[str, Any] = yaml.safe_load((CONFIG / FILE).read_text(encoding="utf-8")) or {}
    return parse(doc)


# The newest recorded opening of each country (verify.country_opening, CR-127), held in this process. The file lists the countries; an operator's
# recorded decision opens or closes REGISTRATION for one of them. `set_openings` is called at start-up, after a change and on a short timer.
_openings: dict[OpeningKey, Opening] = {}
_merged: Registry | None = None
_loaded_at = 0.0
REFRESH_SECONDS = 20.0


def set_openings(openings: dict[OpeningKey, Opening]) -> None:
    global _openings, _merged, _loaded_at
    _openings, _merged, _loaded_at = dict(openings), None, time.monotonic()


def openings() -> dict[OpeningKey, Opening]:
    return dict(_openings)


def openings_stale() -> bool:
    return time.monotonic() - _loaded_at > REFRESH_SECONDS


def registry() -> Registry:
    """The file's registry with each country's recorded opening applied."""
    global _merged
    if _merged is None:
        _merged = apply_openings(file_registry(), _openings)
    return _merged


def _reset() -> None:
    global _merged, _openings, _loaded_at
    file_registry.cache_clear()
    _merged, _openings, _loaded_at = None, {}, 0.0


registry.cache_clear = _reset  # type: ignore[attr-defined]  # as before: a test or a config reload starts again from the file


__all__ = [
    "CONFIG",
    "file_registry",
    "openings",
    "openings_stale",
    "parse",
    "registry",
    "set_openings",
]
