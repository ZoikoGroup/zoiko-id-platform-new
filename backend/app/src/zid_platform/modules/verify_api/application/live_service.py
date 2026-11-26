"""The gate for a PRODUCTION verification session (CR-164; docs/specs/verify-api-live-check-product-definition.md).

A production session is made only when ALL of these hold, and it is refused BEFORE the customer is shown anything:
1. the company has an active production grant (the operators' go-live approval, signed);
2. the workflow is the one the live check serves, the grant names the face match check, and the grant names the customer's country;
3. the cell can actually serve that country: the owners have published a policy for it (or accepted the global policy in writing) that offers the automatic face route, and have acknowledged that the route runs no
   liveness test and has not been formally evaluated.

The composition root supplies how to read the cell's identity engine services; with none supplied, nothing live is ever served.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.verification.ports import CaptureError, PartnerServices, partner_capture
from zid_platform.modules.verify_api.domain import live as lv
from zid_platform.modules.verify_api.domain import model as m
from zid_platform.modules.verify_api.infrastructure import go_live_store

Provider = Callable[[], PartnerServices]
_provider: Provider | None = None


def configure(provider: Provider | None) -> None:
    """The composition root says how to read the running cell's engine services (scanner, imaging, policies, acknowledgements) at the moment a session is made."""
    global _provider
    _provider = provider


def services() -> PartnerServices | None:
    return _provider() if _provider is not None else None


async def require_enabled(conn: AsyncConnection, clean: dict[str, Any]) -> None:
    """Refuse a production session that cannot be served, with the reason in the partner's own words. Inside the company's scope."""
    grant = await go_live_store.active_grant(conn)
    if grant is None:
        raise m.PartnerError(
            "PRODUCT_NOT_ENTITLED",
            "Live verification is not open for this company. Ask for production access first.",
        )
    lv.check_live_eligible(
        country=clean["country"],
        workflow_id=clean["workflow_id"],
        grant_markets=list(grant["markets"]),
        grant_methods=list(grant["methods"]),
    )
    sv = services()
    try:
        rules = partner_capture.enabled_rules(sv, clean["country"]) if sv is not None else None
    except CaptureError:
        rules = None
    if rules is None:
        raise m.PartnerError(
            "MARKET_NOT_ENABLED",
            f"Live verification is not enabled for {clean['country']} yet. Nothing was created.",
        )
