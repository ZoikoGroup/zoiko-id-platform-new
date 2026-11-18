"""Release gates for a screening module in a market, operator plane (ZID-DTV-WF-06 section 22, ENG-20, QA-053; CR-113).

    GET  /v1/screening/gates/{market}/{module}                      the four gates, which roles are still missing, open defects, holds, and whether the scope is GO        (any screening capability)
    POST /v1/screening/gates/{market}/{module}:sign                 sign a gate in the role the caller's capability names, with an evidence reference and a validity      (screening.gate.sign_<role>)
    POST /v1/screening/gates/records/{id}:rescind                   a signer withdraws their own signature                                                                   (a signing capability, the signer)
    POST /v1/screening/gates/{market}/{module}/defects              open a P0 or critical defect: every gate stops while it is open                                         (screening.gate.defect)
    POST /v1/screening/gates/records/{id}:close                     close a defect                                                                                           (screening.gate.defect)
    POST /v1/screening/gates/{market}/{module}/holds                a kill switch, at once, by one person                                                                    (screening.gate.hold)
    POST /v1/screening/gates/records/{id}:release                   release a hold: never the person who placed it                                                           (screening.gate.release_hold)

The role someone signs in is the capability they hold, and nobody holds two. A sign-off cites its evidence, expires (at most a year), and counts only for its own market and module. A module runs in a market only
while every gate is signed by its named roles, no P0 defect is open and no hold is in place.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Request
from pydantic import BaseModel, ConfigDict, Field
from zid_db import tenant_transaction
from zid_errors import ZidError

from zid_platform.api_auth import request_engine
from zid_platform.modules.operator_access.api.screening_sources import READERS, sc
from zid_platform.modules.operator_access.domain.model import SCREENING_GATE_ROLES
from zid_platform.modules.operator_access.ports import has_capability, require_operator
from zid_platform.operator_actions import Actions

DEFECT, HOLD, RELEASE = (
    "screening.gate.defect",
    "screening.gate.hold",
    "screening.gate.release_hold",
)
MarketPath = Annotated[str, Path(pattern=r"^[A-Za-z]{2}$")]
ModulePath = Annotated[str, Path(pattern=r"^(SANCTIONS|PEP_RCA|ADVERSE_MEDIA|MONITORING)$")]
IdPath = Annotated[uuid.UUID, Path()]

router = APIRouter(tags=["screening-gates"])
OperatorDep = Annotated[Any, Depends(require_operator)]

actions = Actions(
    namespace=uuid.UUID("a9e61f04-3c72-4d85-b0a1-5f28d7c3e960"),
    refused_event="screening.gate.refused",
    status={
        "SCOPE_INVALID": 422,
        "GATE_UNKNOWN": 422,
        "ROLE_NOT_FOR_GATE": 403,
        "EVIDENCE_REQUIRED": 422,
        "VALIDITY_INVALID": 422,
        "ROLE_ALREADY_SIGNED": 409,
        "SIGNER_HOLDS_ANOTHER_ROLE": 403,
        "SIGNER_ALREADY_SIGNED": 403,
        "RECORD_NOT_FOUND": 404,
        "RECORD_ALREADY_CLOSED": 409,
        "HOLD_RELEASED_BY_ITS_OWN_PLACER": 403,
        "ONLY_THE_SIGNER_RESCINDS": 403,
    },
    errors=lambda: (sc.ScreeningError,),
    refused_code="SCREENING_GATE_REFUSED",
    capability_hint="a screening gate capability",
)


class SignIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    gate: str = Field(pattern=r"^G[0-3]$")
    evidence_ref: str = Field(min_length=1, max_length=500)
    valid_days: int = Field(default=180, ge=1, le=365)


class ReasonIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ref: str = Field(min_length=3, max_length=500)


async def _signing_role(conn: Any, op: Any, resource: str) -> str:
    """The gate role this operator signs in, from the capability they hold. Nobody holds two (a conflict rule), so the answer is one or none."""
    for role in SCREENING_GATE_ROLES:
        if await has_capability(
            conn,
            str(op.principal_id),
            f"screening.gate.sign_{role}",
            None,
            resource,
            datetime.now(UTC),
        ):
            return role.upper()
    raise ZidError(
        "OPERATOR_CAPABILITY_REQUIRED",
        "This needs a screening gate signing capability.",
        http_status=403,
    )


@router.get("/v1/screening/gates/{market}/{module}")
async def gate_status(
    market: MarketPath, module: ModulePath, request: Request, op: OperatorDep
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await actions.need_any(conn, op, READERS, f"regulatory:{market}-{module}")
        try:
            return dict(await sc.gates.status(conn, market, module, now=datetime.now(UTC)))
        except sc.ScreeningError as e:
            raise actions.fail(e) from e


@router.post("/v1/screening/gates/{market}/{module}:sign")
async def sign(
    market: MarketPath, module: ModulePath, body: SignIn, request: Request, op: OperatorDep
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        role = await _signing_role(conn, op, f"regulatory:{market}-{module}")

    async def work(conn: Any, now: datetime) -> tuple[dict[str, Any], str, dict[str, Any]]:
        out = await sc.gates.sign(
            conn,
            market,
            module,
            gate=body.gate,
            role=role,
            signer=str(op.principal_id),
            evidence_ref=body.evidence_ref,
            valid_days=body.valid_days,
            now=now,
        )
        return (
            out,
            out["record_id"],
            {
                "market": market.upper(),
                "module": module,
                "gate": body.gate,
                "go": out["go"],
                "valid_days": body.valid_days,
            },
        )

    return await actions.act(
        request, op, event="screening.gate.signed", action="sign_gate", capability=f"screening.gate.sign_{role.lower()}", target_type="screening_gate", target=f"{market}-{module}", work=work, role=role,
    )  # fmt: skip


@router.post("/v1/screening/gates/records/{record_id}:rescind")
async def rescind(record_id: IdPath, request: Request, op: OperatorDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        role = await _signing_role(conn, op, f"regulatory:{record_id}")

    async def work(conn: Any, now: datetime) -> tuple[dict[str, Any], str, dict[str, Any]]:
        owner = await sc.gates.signer_of(conn, str(record_id))
        if owner is not None and owner != str(op.principal_id):
            raise sc.ScreeningError(
                "ONLY_THE_SIGNER_RESCINDS", "a signature is withdrawn by the person who gave it"
            )
        out = await sc.gates.close(
            conn, str(record_id), by=str(op.principal_id), kinds=("SIGNOFF",), now=now
        )
        return out, str(record_id), {"go": out["go"]}

    return await actions.act(
        request, op, event="screening.gate.rescinded", action="rescind_gate", capability=f"screening.gate.sign_{role.lower()}", target_type="screening_gate", target=str(record_id), work=work, role=role,
    )  # fmt: skip


async def _record(
    request: Request,
    op: Any,
    event: str,
    action: str,
    capability: str,
    market: str,
    module: str,
    body: ReasonIn,
    opener: Any,
) -> dict[str, Any]:
    async def work(conn: Any, now: datetime) -> tuple[dict[str, Any], str, dict[str, Any]]:
        out = await opener(
            conn,
            market,
            module,
            actor=str(op.principal_id),
            now=now,
            **{"ref" if "defect" in action else "reason": body.ref},
        )
        return out, out["record_id"], {"market": market.upper(), "module": module, "go": out["go"]}

    return await actions.act(
        request,
        op,
        event=event,
        action=action,
        capability=capability,
        target_type="screening_gate",
        target=f"{market}-{module}",
        work=work,
    )


@router.post("/v1/screening/gates/{market}/{module}/defects", status_code=201)
async def open_defect(
    market: MarketPath, module: ModulePath, body: ReasonIn, request: Request, op: OperatorDep
) -> dict[str, Any]:
    return await _record(
        request,
        op,
        "screening.gate.defect_opened",
        "open_defect",
        DEFECT,
        market,
        module,
        body,
        sc.gates.open_defect,
    )


@router.post("/v1/screening/gates/{market}/{module}/holds", status_code=201)
async def place_hold(
    market: MarketPath, module: ModulePath, body: ReasonIn, request: Request, op: OperatorDep
) -> dict[str, Any]:
    return await _record(
        request,
        op,
        "screening.gate.hold_placed",
        "place_hold",
        HOLD,
        market,
        module,
        body,
        sc.gates.place_hold,
    )


async def _close(
    request: Request,
    op: Any,
    record_id: uuid.UUID,
    event: str,
    action: str,
    capability: str,
    kinds: tuple[str, ...],
) -> dict[str, Any]:
    async def work(conn: Any, now: datetime) -> tuple[dict[str, Any], str, dict[str, Any]]:
        out = await sc.gates.close(
            conn, str(record_id), by=str(op.principal_id), kinds=kinds, now=now
        )
        return out, str(record_id), {"go": out["go"]}

    return await actions.act(
        request,
        op,
        event=event,
        action=action,
        capability=capability,
        target_type="screening_gate",
        target=str(record_id),
        work=work,
    )


@router.post("/v1/screening/gates/records/{record_id}:close")
async def close_defect(record_id: IdPath, request: Request, op: OperatorDep) -> dict[str, Any]:
    return await _close(
        request, op, record_id, "screening.gate.defect_closed", "close_defect", DEFECT, ("DEFECT",)
    )


@router.post("/v1/screening/gates/records/{record_id}:release")
async def release_hold(record_id: IdPath, request: Request, op: OperatorDep) -> dict[str, Any]:
    return await _close(
        request, op, record_id, "screening.gate.hold_released", "release_hold", RELEASE, ("HOLD",)
    )


__all__ = ["router"]
