"""Country availability (ZID-EDR-001): which capabilities are open in which country, truthfully.

    GET /v1/countries     every listed country with DISABLED | SANDBOX | ENABLED per capability

Public by design: the sign-up country selector and the documentation need it before anyone has an account.
It names no provider, approval or legal position, only what a caller may rely on right now. A country the
registry does not list is not available; there is no other source of truth for it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Request

from zid_platform.modules.verification.domain.countries import public_view
from zid_platform.modules.verification.infrastructure.country_registry import registry

router = APIRouter(tags=["countries"])


@router.get("/v1/countries")
async def countries(request: Request) -> dict[str, Any]:
    reg = registry()
    return {
        "version": reg.version,
        "countries": public_view(
            reg,
            now=datetime.now(UTC),
            environment=str(getattr(request.app.state, "environment", "production")),
        ),
    }
