"""Load the country capability registry (`backend/config/country-capabilities`; ZID_PLATFORM_CONFIG overrides
the config root). Versioned configuration: a country is switched on or off by editing the file, never code."""

from __future__ import annotations

import os
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
    Registry,
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
def registry() -> Registry:
    doc: dict[str, Any] = yaml.safe_load((CONFIG / FILE).read_text(encoding="utf-8")) or {}
    return parse(doc)


__all__ = ["CONFIG", "parse", "registry"]
