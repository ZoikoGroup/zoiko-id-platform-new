"""POST /v2/travel-rule/validate (ZID-GAP-001 v2.0 DTV-33; CR-097): is an IVMS101 payload well formed, and is the counterparty one we can name.

Stateless and read-only: it sends nothing to a partner, stores nothing and decides nothing about releasing a transfer. It runs behind the product line gate,
and the shipped configuration lists `travel_rule` as DESIGN, so until the owners record a legal basis and a partner contract it answers SERVICE_UNAVAILABLE.
The answer is a set of findings (a code and a path, never a value), the state of each counterparty VASP, and whether the transfer is at or above the legal
threshold, which is UNKNOWN when none is configured. See `verification/domain/travel_rule.py` for the rules.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from zid_context import current_context
from zid_db import tenant_transaction
from zid_errors import ZidError

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.modules.verification.application import travel_rule_service as svc
from zid_platform.modules.verification.domain import travel_rule as tr
from zid_platform.modules.verification.domain.product_lines import (
    ProductLineError,
    require_available,
)
from zid_platform.modules.verification.infrastructure import travel_rule_store as store
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


class TravelRuleRecordIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    direction: Literal["OUTGOING", "INCOMING"]
    jurisdiction: str = Field(pattern=r"^[A-Z]{2}$")
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


def _gate(request: Request, jurisdiction: str) -> None:
    lines = getattr(request.app.state, "product_lines", None) or product_line_registry()
    try:
        require_available(lines, tr.LINE, jurisdiction)
    except ProductLineError as e:
        detail = str(e).split(": ", 1)[1] if ": " in str(e) else ""
        raise ZidError(
            e.code,
            e.code.replace("_", " ").capitalize() + "." + (f" {detail}." if detail else ""),
            http_status=409,
        ) from e


@router.post("/v2/travel-rule/messages")
async def record_message(
    body: TravelRuleRecordIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    """Record a message: what was found about it, never the message. Nothing is sent, and nothing is decided about the transfer."""
    if not caller.ctx.principal_id or not caller.ctx.tenant_id:
        raise ZidError(
            "UNAUTHENTICATED", "An authenticated principal is required.", http_status=401
        )
    _gate(request, body.jurisdiction)
    registry = getattr(request.app.state, "vasp_registry", None) or vasp_registry()
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn,
            caller,
            action="verification.travel_rule.record",
            resource="travel-rule:message",
        )
        try:
            row, created = await svc.record(
                conn,
                caller.ctx.tenant_id,
                actor=caller.ctx.principal_id,
                direction=body.direction,
                jurisdiction=body.jurisdiction,
                payload=body.ivms101,
                registry=registry,
                now=datetime.now(UTC),
            )
        except tr.TravelRuleError as e:
            raise ZidError(
                e.code, e.code.replace("_", " ").capitalize() + ".", http_status=422
            ) from e
    return {
        **_ids(),
        **svc.view(row),
        "already_recorded": not created,
        "notes": [
            "Only what was found is kept: the people in the message are not stored.",
            "Nothing was sent to a partner. A person decides whether to release or hold the transfer.",
        ],
    }


@router.get("/v2/travel-rule/messages")
async def list_messages(request: Request, caller: CallerDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn,
            caller,
            action="verification.travel_rule.read",
            resource="travel-rule:message",
        )
        rows = await store.recent(conn)
    return {**_ids(), "items": [svc.view(r) for r in rows]}


@router.get("/v2/travel-rule/messages/{message_id}")
async def get_message(message_id: str, request: Request, caller: CallerDep) -> dict[str, Any]:
    import uuid as _uuid

    try:
        _uuid.UUID(message_id)
    except ValueError as e:
        raise ZidError("MESSAGE_NOT_FOUND", "Message not found.", http_status=404) from e
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn,
            caller,
            action="verification.travel_rule.read",
            resource=f"travel-rule:message:{message_id}",
        )
        row = await store.get(conn, message_id)
    if row is None:
        raise ZidError("MESSAGE_NOT_FOUND", "Message not found.", http_status=404)
    return {**_ids(), **svc.view(row)}
