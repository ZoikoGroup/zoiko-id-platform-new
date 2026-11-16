"""POST /v2/travel-rule/validate (ZID-GAP-001 v2.0 DTV-33; CR-097): is an IVMS101 payload well formed, and is the counterparty one we can name.

Stateless and read-only: it sends nothing to a partner, stores nothing and decides nothing about releasing a transfer. It runs behind the product line gate,
and the shipped configuration lists `travel_rule` as DESIGN, so until the owners record a legal basis and a partner contract it answers SERVICE_UNAVAILABLE.
The answer is a set of findings (a code and a path, never a value), the state of each counterparty VASP, and whether the transfer is at or above the legal
threshold, which is UNKNOWN when none is configured. See `verification/domain/travel_rule.py` for the rules.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from zid_context import current_context
from zid_db import tenant_transaction
from zid_errors import ZidError

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.modules.verification.domain import travel_rule as tr
from zid_platform.modules.verification.domain.product_lines import (
    ProductLineError,
    require_available,
)
from zid_platform.modules.verification.infrastructure.product_line_registry import (
    product_line_registry,
)
from zid_platform.modules.verification.infrastructure.vasp_registry import vasp_registry
from zid_platform.protected import protected_action

router = APIRouter(tags=["verification-v2"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]


class TravelRuleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    jurisdiction: str = Field(
        pattern=r"^[A-Z]{2}$"
    )  # where the transfer is regulated, which chooses the counterparty and threshold rules
    ivms101: dict[str, Any]


def _ids() -> dict[str, str]:
    ctx = current_context()
    return {
        "request_id": ctx.request_id if ctx else "req_unknown",
        "correlation_id": ctx.correlation_id if ctx else "cor_unknown",
    }


@router.post("/v2/travel-rule/validate")
async def validate(body: TravelRuleIn, request: Request, caller: CallerDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn,
            caller,
            action="verification.travel_rule.validate",
            resource="travel-rule:validate",
        )
    lines = getattr(request.app.state, "product_lines", None) or product_line_registry()
    try:
        require_available(lines, tr.LINE, body.jurisdiction)
    except ProductLineError as e:
        detail = str(e).split(": ", 1)[1] if ": " in str(e) else ""
        raise ZidError(
            e.code,
            e.code.replace("_", " ").capitalize() + "." + (f" {detail}." if detail else ""),
            http_status=409,
        ) from e
    registry = getattr(request.app.state, "vasp_registry", None) or vasp_registry()
    try:
        result = tr.assess(body.ivms101, registry, jurisdiction=body.jurisdiction)
    except tr.TravelRuleError as e:
        raise ZidError(e.code, e.code.replace("_", " ").capitalize() + ".", http_status=422) from e
    return {
        **_ids(),
        "well_formed": result.well_formed,
        "findings": [{"code": f.code, "path": f.path} for f in result.findings],
        "originating_vasp": result.originating_vasp.value,
        "beneficiary_vasp": result.beneficiary_vasp.value,
        "threshold": result.threshold.value,
        "registry_version": registry.version,
        "notes": list(result.notes),
    }
