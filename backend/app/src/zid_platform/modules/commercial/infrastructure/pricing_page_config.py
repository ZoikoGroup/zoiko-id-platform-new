"""Load the public pricing page structure (`backend/config/commercial/pricing-page.yaml`; ZID_PLATFORM_CONFIG overrides the root). Read once; a file that does not validate stops the load."""

from __future__ import annotations

import hashlib
from functools import lru_cache

import yaml

from zid_platform.modules.commercial.domain.pricing_page import PricingPage, parse
from zid_platform.modules.commercial.infrastructure.bands import CONFIG

_FILE = CONFIG / "commercial" / "pricing-page.yaml"


@lru_cache(maxsize=1)
def pricing_page() -> PricingPage:
    doc = yaml.safe_load(_FILE.read_text(encoding="utf-8")) or {}
    return parse(doc)


def content_hash() -> str:
    """The hash of the page file exactly as shipped: what the Founder and the CCO sign. Changing a single character makes the page unsigned again."""
    return hashlib.sha256(_FILE.read_bytes()).hexdigest()


__all__ = ["content_hash", "pricing_page"]
