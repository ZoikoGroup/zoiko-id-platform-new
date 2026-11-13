"""The public service-offerings catalog (Global Service Offerings, Nov 2026; CR-088).

    GET /v1/offerings              every offering available somewhere, with the countries where it is
    GET /v1/offerings?country=GB   what is available in one country

Public by design, like GET /v1/countries: the website and the documentation show it. It lists an offering in a country only when the
publish-coverage rule holds there (provider rights, country rules, privacy controls and independently accepted tests), using the same checks
as the regulated entry points. It shows the approved wording only: no provider, test case, reason or certification claim. Anything not
available is simply absent.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request

from zid_platform.api_auth import request_engine
from zid_platform.modules.verification.application import coverage
from zid_platform.modules.verification.infrastructure.country_registry import registry
from zid_platform.modules.verification.infrastructure.offerings_catalog import offerings_catalog
from zid_platform.modules.verification.infrastructure.privacy_overlays import privacy_overlays

router = APIRouter(tags=["countries"])


@router.get("/v1/offerings")
async def public_offerings(
    request: Request,
    country: Annotated[str | None, Query(pattern=r"^[A-Z]{2}$")] = None,
) -> dict[str, Any]:
    catalog = offerings_catalog()
    async with request_engine(request).begin() as conn:  # platform records only: no tenant
        cov = await coverage.offerings_coverage(
            conn,
            catalog,
            registry(),
            overlays=privacy_overlays(),
            now=datetime.now(UTC),
            countries=[country] if country else None,
        )
    where: dict[str, list[str]] = {}
    for c in cov:
        if c.available:
            where.setdefault(c.offering_id, []).append(c.country)
    return {
        "items": [
            {
                "offering_id": o.offering_id,
                "title": o.title,
                "wording": o.wording,
                "countries": where[o.offering_id],
            }
            for o in catalog
            if o.offering_id in where
        ]
    }
