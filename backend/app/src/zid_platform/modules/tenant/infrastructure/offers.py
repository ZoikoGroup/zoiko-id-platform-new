"""Load the residency offers and the home-cell readiness (`backend/config/residency-offers`, `cells`;
ZID_PLATFORM_CONFIG overrides the config root). Versioned configuration, not tenant data."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from zid_platform.modules.tenant.domain.offers import (
    CellReadiness,
    HomeRegion,
    Offer,
    Offers,
    validate,
)
from zid_platform.modules.tenant.infrastructure.catalog import CONFIG, _yaml


def parse(offers_doc: dict[str, Any], readiness_doc: dict[str, Any]) -> Offers:
    offers = {
        code: Offer(
            code=code,
            profile=str(body["profile"]),
            availability=str(body["availability"]),
            sensitive_data=bool(body.get("sensitive_data", True)),
            customer_promise=str(body.get("customer_promise", "")),
        )
        for code, body in (offers_doc.get("offers") or {}).items()
    }
    regions = {
        region: HomeRegion(
            region=region,
            cell_id=str(body["cell_id"]),
            wave=body.get("wave"),
            serves=str(body.get("serves", "")),
        )
        for region, body in (offers_doc.get("home_regions") or {}).items()
    }
    readiness = {
        cell: CellReadiness(
            status=str(body.get("status", "NOT_READY")),
            gates=dict(body.get("gates") or {}),
            suppliers={str(k): dict(v or {}) for k, v in (body.get("suppliers") or {}).items()},
        )
        for cell, body in (readiness_doc.get("cells") or {}).items()
    }
    result = Offers(
        version=str(offers_doc.get("version", "0")),
        offers=offers,
        home_regions=regions,
        forbidden_claims=tuple(offers_doc.get("forbidden_claims") or ()),
        readiness=readiness,
        readiness_version=str(readiness_doc.get("version", "0")),
    )
    validate(result)
    return result


@lru_cache(maxsize=1)
def offers() -> Offers:
    return parse(
        _yaml(CONFIG / "residency-offers" / "residency-offers.yaml"),
        _yaml(CONFIG / "cells" / "cell-readiness.yaml"),
    )


__all__ = ["offers", "parse"]
