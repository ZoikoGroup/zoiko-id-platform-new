"""Licensed screening providers, operator plane, read only (ZID-DTV-WF-06 S11, ENG-13; CR-113).

    GET  /v1/screening/providers      each provider in the registry: its state, what its contract covers, who authorised its price, its caps, whether this process would call it now,
                                      and every control that still stops it being used, by code                                                              (any screening capability)

Providers are set in the versioned registry (`config/screening/providers.yaml`), changed by a reviewed change to that file, and never by a click: the configure and the spend-authorisation capabilities are
for the people who make and approve that change, and this view is how they and Finance see its effect. Nothing here calls a provider.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from zid_db import tenant_transaction

from zid_platform.api_auth import request_engine
from zid_platform.modules.operator_access.api.screening_sources import READERS, actions, sc
from zid_platform.modules.operator_access.ports import require_operator

router = APIRouter(tags=["screening-providers"])
OperatorDep = Annotated[Any, Depends(require_operator)]


@router.get("/v1/screening/providers")
async def providers(request: Request, op: OperatorDep) -> dict[str, Any]:
    registry = getattr(request.app.state, "screening_providers", None) or sc.screening_providers()
    adapters = getattr(request.app.state, "screening_adapters", None) or {}
    now = datetime.now(UTC)
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await actions.need_any(conn, op, READERS, "regulatory:screening-providers")
    items = []
    for provider_id, entry in sorted(registry.items()):
        p = entry.provider
        blocked = sc.provider_controls.activation_problems(p)
        if p.state.value == "APPROVED" and provider_id not in adapters:
            blocked.append("ADAPTER_NOT_REGISTERED")
        items.append(
            {
                "provider_id": provider_id,
                "adapter": entry.adapter,
                "adapter_registered": provider_id in adapters,
                "state": p.state.value,
                "classes": sorted(p.classes),
                "markets": sorted(p.markets),
                "regions": sorted(p.regions),
                "contract_version": p.contract_version,
                "price_version": p.price_version,
                "finance_authorised_by": p.finance_authorised_by,
                "unit_cost": str(p.unit_cost),
                "monthly_budget": None if p.monthly_budget is None else str(p.monthly_budget),
                "monthly_quota": p.monthly_quota,
                "blocked_by": blocked,
                "circuit": sc.licensed.circuit_status(provider_id, now),
            }
        )
    return {"items": items}


__all__ = ["router"]
