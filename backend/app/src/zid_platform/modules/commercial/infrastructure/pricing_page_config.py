"""Load the public pricing page structure (`backend/config/commercial/pricing-page.yaml`; ZID_PLATFORM_CONFIG overrides the root). Read once; a file that does not validate stops the load."""

from __future__ import annotations

from functools import lru_cache

import yaml

from zid_platform.modules.commercial.domain.pricing_page import PricingPage, parse
from zid_platform.modules.commercial.infrastructure.bands import CONFIG


@lru_cache(maxsize=1)
def pricing_page() -> PricingPage:
    doc = (
        yaml.safe_load((CONFIG / "commercial" / "pricing-page.yaml").read_text(encoding="utf-8"))
        or {}
    )
    return parse(doc)


__all__ = ["pricing_page"]
