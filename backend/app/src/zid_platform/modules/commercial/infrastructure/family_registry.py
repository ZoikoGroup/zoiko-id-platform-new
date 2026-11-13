"""Load the service-family catalogue (`backend/config/commercial/service-families.yaml`; ZID_PLATFORM_CONFIG overrides the root).

Read once. A file that does not validate stops the load: there is no partial catalogue. A meter a family names must exist in the meter register.
"""

from __future__ import annotations

from functools import lru_cache

import yaml

from zid_platform.modules.commercial.domain.service_families import FamilyRegistry, parse
from zid_platform.modules.commercial.infrastructure.bands import CONFIG, meter_register


@lru_cache(maxsize=1)
def family_registry() -> FamilyRegistry:
    doc = (
        yaml.safe_load(
            (CONFIG / "commercial" / "service-families.yaml").read_text(encoding="utf-8")
        )
        or {}
    )
    return parse(doc, known_meters=set(meter_register()))


__all__ = ["family_registry"]
