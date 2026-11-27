"""Open or close a feature for a country from the Operator Console (ZID-EDR-001 section 4; CR-127).

    GET /v1/regulatory/country-openings              every country the registry lists, and for each feature what the file says, what is recorded and what is in force now
    PUT /v1/regulatory/country-openings/{country}    OPEN one feature (six approvals, the date it takes effect, and for sign-up the cell that holds the accounts) or CLOSE it

A feature is one regulated capability: sign-up (REGISTRATION), identity checks (INDIVIDUAL_VERIFICATION), organization claims (BUSINESS_VERIFICATION),
credentials (CREDENTIAL_ISSUANCE) and enterprise use (ENTERPRISE_USE). Each is its own go/no-go in each country.

The six approvals are the operator's record of decisions the owners made elsewhere; this API cannot make them. It refuses an opening that is not complete, a
country the registry file does not list and a cell this deployment does not know. Every change is one operator action: the capability, the append-only record
and the protected audit record in one transaction (operator_actions.py). The registry file still names the countries.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Path, Request
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from zid_db import tenant_transaction

from zid_platform.api_auth import request_engine
from zid_platform.modules.operator_access.ports import require_operator
from zid_platform.modules.tenant.ports import cell_catalog
from zid_platform.operator_actions import Actions


class _Verification:
    """The verification module's public interface, imported on first use (avoids a dependency cycle at import time)."""

    def __getattr__(self, name: str) -> Any:
        import zid_platform.modules.verification.ports as ports  # noqa: PLR0402

        return getattr(ports, name)


v = _Verification()

router = APIRouter(tags=["regulatory"])
OperatorDep = Annotated[Any, Depends(require_operator)]

RECORD = "regulatory.country_opening.record"
CountryPath = Annotated[str, Path(pattern=r"^[A-Z]{2}$")]

# What each feature lets people do, so the screen can say what a closed switch is holding back.
FEATURES = (
    ("REGISTRATION", "Sign-up", "People create a Zoiko iD account"),
    ("INDIVIDUAL_VERIFICATION", "Identity checks", "A person's identity is verified"),
    ("BUSINESS_VERIFICATION", "Organization claims", "A company is claimed and verified"),
    ("CREDENTIAL_ISSUANCE", "Credentials", "Verifiable credentials are issued"),
    ("ENTERPRISE_USE", "Enterprise use", "Organization workspaces and approvals"),
)
FeatureName = Literal[
    "REGISTRATION",
    "INDIVIDUAL_VERIFICATION",
    "BUSINESS_VERIFICATION",
    "CREDENTIAL_ISSUANCE",
    "ENTERPRISE_USE",
]

# Who owns each approval (ZID-EDR-001 section 4), so the screen can say whose decision a reference stands for.
APPROVALS = (
    ("legal_assessment", "Legal assessment", "Legal"),
    ("supplier_coverage", "Supplier coverage", "Product / Trust"),
    ("privacy_and_data", "Privacy and data", "Privacy"),
    ("security_and_resilience", "Security and resilience", "Security / SRE"),
    ("local_support", "Local support", "Support"),
    ("signoff", "Sign-off", "Founder"),
)

_STATUS = {
    "COUNTRY_NOT_LISTED": 404,
    "COUNTRY_OPENING_INCOMPLETE": 422,
    "CAPABILITY_NOT_OPENABLE": 422,
    "CELL_UNKNOWN": 422,
}


def _errors() -> tuple[type[BaseException], ...]:
    return (v.CountryError,)


actions = Actions(
    namespace=uuid.UUID("3f6a1d52-8c47-4b19-a5e0-7d2c9b64e183"),
    refused_event="regulatory.country_opening.refused",
    status=_STATUS,
    errors=_errors,
    refused_code="COUNTRY_OPENING_REFUSED",
    capability_hint="the country opening capability",
)


class OpeningIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    capability: FeatureName = "REGISTRATION"
    action: Literal["OPEN", "CLOSE"]
    effective_from: AwareDatetime | None = None
    approvals: dict[str, str] = Field(default_factory=dict, max_length=12)
    home_cells: list[str] = Field(default_factory=list, max_length=4)
    reason: str | None = Field(default=None, min_length=1, max_length=500)


def _iso(d: datetime | None) -> str | None:
    return d.isoformat() if d else None


def _opening_view(o: Any) -> dict[str, Any]:
    return {
        "capability": o.capability,
        "version": o.version,
        "status": o.status,
        "effective_from": _iso(o.effective_from),
        "approvals": dict(o.approvals),
        "home_cells": list(o.home_cells),
        "reason": o.reason,
        "recorded_by": o.recorded_by,
        "recorded_at": _iso(o.recorded_at),
    }


def _cells() -> list[dict[str, Any]]:
    return [
        {"cell_id": c.cell_id, "region": c.region, "country": c.country}
        for c in sorted(cell_catalog().cells.values(), key=lambda c: c.cell_id)
        if not c.recovery_only
    ]


@router.get("/v1/regulatory/country-openings")
async def list_openings(request: Request, op: OperatorDep) -> dict[str, Any]:
    now = datetime.now(UTC)
    env = str(getattr(request.app.state, "environment", "production"))
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await actions.need(conn, op, RECORD, "regulatory:country-openings")
    await v.country_opening_store.refresh(request_engine(request), force=True)
    recorded = v.openings()
    merged = v.registry()
    countries: list[dict[str, Any]] = []
    for code, c in sorted(v.file_registry().countries.items()):
        features: list[dict[str, Any]] = []
        for name, _label, _unlocks in FEATURES:
            capability = v.Capability(name)
            entry = c.capabilities.get(capability)
            opening = recorded.get((code, name))
            features.append(
                {
                    "capability": name,
                    "file_status": entry.availability.value if entry else "DISABLED",
                    "in_force": v.availability(
                        merged, code, capability, now=now, environment=env
                    ).value,
                    "opening": _opening_view(opening) if opening else None,
                }
            )
        countries.append({"country": code, "name": c.name, "wave": c.wave, "features": features})
    return {
        "environment": env,
        "features": [
            {"capability": k, "label": label, "unlocks": unlocks} for k, label, unlocks in FEATURES
        ],
        "approvals": [{"key": k, "label": label, "owner": owner} for k, label, owner in APPROVALS],
        "cells": _cells(),
        "countries": countries,
    }


@router.put("/v1/regulatory/country-openings/{country}")
async def record_opening(
    country: CountryPath, body: OpeningIn, request: Request, op: OperatorDep
) -> dict[str, Any]:
    async def work(conn: Any, now: datetime) -> tuple[dict[str, Any], str, dict[str, Any]]:
        if country not in v.file_registry().countries:
            raise v.CountryError("COUNTRY_NOT_LISTED", f"{country} is not in the country registry.")
        if body.action == "OPEN":
            approvals, cells = v.check_opening(
                v.file_registry(),
                country,
                capability=body.capability,
                effective_from=body.effective_from,
                approvals=body.approvals,
                home_cells=body.home_cells,
            )
            known = {c.cell_id for c in cell_catalog().cells.values() if not c.recovery_only}
            unknown = sorted(set(cells) - known)
            if unknown:
                raise v.CountryError(
                    "CELL_UNKNOWN", f"this deployment does not know the cell {unknown}"
                )
            saved = await v.country_opening_store.record(
                conn,
                country=country,
                capability=body.capability,
                status="OPEN",
                effective_from=body.effective_from,
                approvals=dict(approvals),
                home_cells=cells,
                reason=body.reason,
                recorded_by=str(op.principal_id),
                now=now,
            )
        else:
            if body.capability not in {c.value for c in v.OPENABLE}:
                raise v.CountryError(
                    "CAPABILITY_NOT_OPENABLE", f"{body.capability} cannot be closed here."
                )
            saved = await v.country_opening_store.record(
                conn,
                country=country,
                capability=body.capability,
                status="CLOSED",
                effective_from=None,
                approvals={},
                home_cells=(),
                reason=body.reason,
                recorded_by=str(op.principal_id),
                now=now,
            )
        return (
            _opening_view(saved),
            f"{country}:{saved.capability}:v{saved.version}",
            {
                "capability": saved.capability,
                "status": saved.status,
                "version": saved.version,
                "home_cells": list(saved.home_cells),
            },
        )

    out = await actions.act(
        request,
        op,
        event="regulatory.country_opening.recorded",
        action="record",
        capability=RECORD,
        target_type="country_opening",
        target=f"country:{country}",
        work=work,
    )
    await v.country_opening_store.refresh(
        request_engine(request), force=True
    )  # in force from the next request, in this process
    return out
