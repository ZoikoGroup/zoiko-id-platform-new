"""Retire a market's biometric policy (ZID-DTV-WF-04 QA-039; CR-113).

    POST /v1/regulatory/biometric-methods/markets/{country}:retire   every scope for the country to OFF, open proposals rejected
                                                                     (regulatory.biometric_method.pause: one person, it only stops things)

The audit record (regulatory.biometric_method.market_retired) and the announced event (verification.biometric_method.state_changed) carry the
country, the scope versions written and the proposals withdrawn; never the reason text. Past sessions keep their dispositions.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Request
from pydantic import BaseModel, ConfigDict, Field

from zid_platform.modules.operator_access.ports import require_operator

router = APIRouter(tags=["regulatory"])
OperatorDep = Annotated[Any, Depends(require_operator)]
REF = r"^[A-Za-z0-9][A-Za-z0-9._:/#@+=-]{2,255}$"


def record_view(r: Any) -> dict[str, Any]:
    """A retired version, as the methods list shows one."""
    s = r.scope
    return {
        "scope_id": r.scope_id,
        "version": r.version,
        "state": r.state.value,
        "scope": {
            "method": s.method.value,
            "engine_id": s.engine_id,
            "engine_version": s.engine_version,
            "country": s.country,
            "purpose": s.purpose,
            "pilot_tenant": s.tenant_id,
        },
        "evidence_ref": r.evidence_ref,
        "recorded_by": r.recorded_by,
        "effective_at": r.effective_at.isoformat(),
        "exposure_percent": r.exposure_percent,
    }


CountryPath = Annotated[str, Path(pattern=r"^[A-Z]{2}$")]


class RetireMarketIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=10, max_length=1000)
    evidence_ref: str = Field(pattern=REF)


@router.post("/v1/regulatory/biometric-methods/markets/{country}:retire")
async def retire_market(
    country: CountryPath, body: RetireMarketIn, request: Request, op: OperatorDep
) -> dict[str, Any]:
    from zid_platform.modules.operator_access.api.regulatory_biometric_methods import (
        STATE_CHANGED,
        actions,
    )
    from zid_platform.modules.verification.ports import biometric_markets

    async def work(conn: Any, now: datetime) -> tuple[dict[str, Any], str, dict[str, Any]]:
        r = await biometric_markets.retire_market(
            conn,
            country,
            by=str(op.principal_id),
            reason=body.reason,
            evidence_ref=body.evidence_ref,
            now=now,
        )
        details = {
            "country": r.country,
            "retired": [f"{x.scope_id}:v{x.version}" for x in r.retired],
            "withdrawn_proposals": list(r.withdrawn),
            "already_off": list(r.already_off),
            "evidence_ref": body.evidence_ref,
        }
        return (
            {
                "country": r.country,
                "retired": [record_view(x) for x in r.retired],
                "withdrawn_proposals": list(r.withdrawn),
                "already_off": list(r.already_off),
            },
            f"market:{r.country}",
            details,
        )

    return await actions.act(
        request,
        op,
        event="regulatory.biometric_method.market_retired",
        publish=STATE_CHANGED,
        action="retire-market",
        capability="regulatory.biometric_method.pause",
        target_type="biometric_market",
        target=country,
        work=work,
    )


__all__ = ["RetireMarketIn", "router"]
