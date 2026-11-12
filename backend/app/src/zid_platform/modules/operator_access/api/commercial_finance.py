"""Operator routes for finance authority (ZID-COM-CL-003, ZID-COM-CL-004, ZID-UX-COM-009; slice L-14b, CR-054).

Every route needs an approved, unexpired `commercial.finance.manage` grant. That is the operator's access to the console; WHO MAY approve what is the
appointment table and the signed policy, decided in `commercial/application/finance_service.py`, never here. The server picks the tier and the
approvers; the person only says what they want and signs what they read.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from zid_db import in_tenant, tenant_transaction
from zid_errors import ZidError

from zid_platform.api_auth import request_engine
from zid_platform.modules.commercial.ports import (
    CommitmentError,
    FinanceError,
    FinanceKind,
    commitments,
    eligibility,
    finance,
    finance_policy,
)
from zid_platform.modules.operator_access.api.commercial_admin import OperatorDep, _require

router = APIRouter(tags=["commercial-finance"])

_STATUS = {
    "REQUEST_NOT_FOUND": 404,
    "APPOINTMENT_NOT_FOUND": 404,
    "NOT_A_REQUESTER": 403,
    "NOT_A_REVIEWER": 403,
    "NOT_AN_EXECUTOR": 403,
    "NOT_A_SIGNATORY": 403,
    "NOT_APPOINTED": 403,
    "NOT_THE_REQUESTER": 403,
    "SELF_APPROVAL": 403,
    "SELF_APPOINTMENT": 403,
    "ONE_PERSON_TWO_SLOTS": 403,
    "EXECUTOR_NOT_INDEPENDENT": 403,
    "POLICY_NOT_SIGNED": 403,
    "NOT_A_PERSON": 422,
    "PRINCIPAL_UNKNOWN": 422,
    "PRINCIPAL_NOT_ACTIVE": 422,
}
_VALIDATION = {
    "AMOUNT_INVALID", "REASON_REQUIRED", "CATEGORY_INVALID", "INVOICE_REQUIRED", "INVOICE_NOT_ALLOWED", "PILOT_PERIOD_INVALID",
    "ROLE_UNKNOWN", "SCOPE_INVALID", "CAP_REQUIRED", "WINDOW_INVALID", "DECISION_INVALID", "FX_UNPINNED", "ACCOUNT_REQUIRED", "EVIDENCE_REQUIRED", "PILOT_PROGRAM_REQUIRED", "DISPOSITION_REQUIRED", "DISPOSITION_INVALID",
}  # fmt: skip


def _fail(e: FinanceError) -> ZidError:
    return ZidError(
        e.code, e.detail, http_status=_STATUS.get(e.code, 422 if e.code in _VALIDATION else 409)
    )


class AppointBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    principal_id: uuid.UUID
    role: str = Field(min_length=2, max_length=40)
    effective_from: datetime | None = None
    effective_to: datetime | None = None


class SignBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class RequestBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["CREDIT_NOTE", "PILOT_CREDIT"]
    target_tenant_id: uuid.UUID
    invoice_id: uuid.UUID | None = None
    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    category: Literal["CORRECTION", "GOODWILL", "PILOT"]
    reason: str = Field(min_length=1, max_length=500)
    ends_at: datetime | None = None
    evidence_ref: str | None = Field(default=None, max_length=200)
    disposition: Literal["INVOICE_BALANCE", "REFUND", "CUSTOMER_BALANCE", "OUT_OF_BAND"] | None = (
        None
    )
    program_ref: str | None = Field(default=None, max_length=120)
    sponsor: str | None = Field(default=None, max_length=120)


class DecideBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: str = Field(min_length=2, max_length=40)
    decision: Literal["APPROVE", "REJECT"]
    snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    reason: str | None = Field(default=None, max_length=500)


class ExecuteBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    idempotency_key: str = Field(min_length=8, max_length=128)


def _env(request: Request) -> str:
    return str(getattr(request.app.state, "environment", "production"))


async def _gate(conn: Any, op: Any) -> None:
    await _require(conn, op, "commercial.finance.manage", None, "commercial:finance")


def _tiers(inst: Any) -> list[dict[str, Any]]:
    return [
        {
            "max_usd": None if t.max_minor is None else f"{Decimal(t.max_minor) / 100:.2f}",
            "roles": list(t.roles),
        }
        for t in inst.tiers
    ]


@router.get("/v1/commercial/finance/policy")
async def get_policy(request: Request, op: OperatorDep) -> dict[str, Any]:
    p = finance_policy()
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _gate(conn, op)
        state = await finance.policy_state(conn)
    return {
        **state,
        "environment": _env(request),
        "locked": _env(request) not in finance.SANDBOX and not state["effective"],
        "unit": "USD",
        "request_roles": list(p.request_roles),
        "execute_roles": list(p.execute_roles),
        "request_valid_days": p.request_valid_days,
        "approval_valid_days": p.approval_valid_days,
        "credit_note": {"window_days": p.credit_note.window_days, "tiers": _tiers(p.credit_note)},
        "pilot_credit": {
            "window_days": p.pilot_credit.window_days,
            "max_days": p.pilot_credit.max_days,
            "tiers": _tiers(p.pilot_credit),
        },
    }


@router.post("/v1/commercial/finance/policy:sign")
async def sign_policy(body: SignBody, request: Request, op: OperatorDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _gate(conn, op)
        try:
            return await finance.sign_policy(
                conn, signer=op.principal_id, content_hash=body.content_hash
            )
        except FinanceError as e:
            raise _fail(e) from e


@router.get("/v1/commercial/finance/appointments")
async def list_appointments(request: Request, op: OperatorDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _gate(conn, op)
        return {"items": await finance.appointments(conn), "roles": list(finance.ROLES)}


@router.post("/v1/commercial/finance/appointments", status_code=201)
async def appoint(body: AppointBody, request: Request, op: OperatorDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _gate(conn, op)
        try:
            return await finance.appoint(
                conn, principal=str(body.principal_id), role=body.role, appointed_by=op.principal_id,
                effective_from=body.effective_from or datetime.now(UTC), effective_to=body.effective_to,
            )  # fmt: skip
        except FinanceError as e:
            raise _fail(e) from e


@router.post("/v1/commercial/finance/appointments/{appointment_id}:end")
async def end_appointment(
    appointment_id: uuid.UUID, request: Request, op: OperatorDep
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _gate(conn, op)
        try:
            await finance.end_appointment(conn, str(appointment_id), by=op.principal_id)
        except FinanceError as e:
            raise _fail(e) from e
    return {"appointment_id": str(appointment_id), "ended": True}


@router.post("/v1/commercial/finance/requests", status_code=201)
async def open_request(body: RequestBody, request: Request, op: OperatorDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _gate(conn, op)
        try:
            return await finance.open_request(
                conn, kind=FinanceKind(body.kind), tenant_id=str(body.target_tenant_id), invoice_id=str(body.invoice_id) if body.invoice_id else None,
                amount=body.amount, currency=body.currency, category=body.category, reason=body.reason, requested_by=op.principal_id,
                ends_at=body.ends_at, evidence_ref=body.evidence_ref, disposition=body.disposition, program_ref=body.program_ref, sponsor=body.sponsor, environment=_env(request),
            )  # fmt: skip
        except FinanceError as e:
            raise _fail(e) from e


@router.get("/v1/commercial/finance/requests")
async def list_requests(
    request: Request,
    op: OperatorDep,
    state: Annotated[str | None, Query(max_length=30)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _gate(conn, op)
        return {"items": await finance.list_requests(conn, state=state, limit=limit)}


@router.get("/v1/commercial/finance/requests/{request_id}")
async def get_request(request_id: uuid.UUID, request: Request, op: OperatorDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _gate(conn, op)
        try:
            return await finance.get_request(conn, str(request_id))
        except FinanceError as e:
            raise _fail(e) from e


@router.post("/v1/commercial/finance/requests/{request_id}:decide")
async def decide_request(
    request_id: uuid.UUID, body: DecideBody, request: Request, op: OperatorDep
) -> dict[str, Any]:
    refused: FinanceError | None = None
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _gate(conn, op)
        try:
            return await finance.decide(
                conn, str(request_id), role=body.role, approver=op.principal_id, decision=body.decision, presented_hash=body.snapshot_hash,
                reason=body.reason, environment=_env(request),
            )  # fmt: skip
        except FinanceError as e:
            refused = e  # raised after the transaction ends, so a lapse written on the way is kept
    raise _fail(refused)


@router.post("/v1/commercial/finance/requests/{request_id}:cancel")
async def cancel_request(
    request_id: uuid.UUID, request: Request, op: OperatorDep
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _gate(conn, op)
        try:
            return await finance.cancel(conn, str(request_id), by=op.principal_id)
        except FinanceError as e:
            raise _fail(e) from e


@router.post("/v1/commercial/finance/requests/{request_id}:execute")
async def execute_request(
    request_id: uuid.UUID, body: ExecuteBody, request: Request, op: OperatorDep
) -> dict[str, Any]:
    refused: FinanceError | None = None
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _gate(conn, op)
        try:
            return await finance.execute(
                conn,
                str(request_id),
                executor=op.principal_id,
                idempotency_key=body.idempotency_key,
                environment=_env(request),
            )
        except FinanceError as e:
            refused = e  # a voided approval or a lapse written on the way is kept; the refusal is raised once the transaction has committed
    raise _fail(refused)


@router.post("/v1/commercial/finance/commitments/{target_tenant_id}/{request_id}:review")
async def review_commitment(
    target_tenant_id: uuid.UUID, request_id: uuid.UUID, request: Request, op: OperatorDep
) -> dict[str, Any]:
    """A Controller or a CFO marks a customer's nonstandard terms reviewed (contract, legal and finance), once; only then do the customer's signatures count."""
    refused: CommitmentError | None = None
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _gate(conn, op)
        async with in_tenant(conn, str(target_tenant_id)):
            try:
                return await commitments.mark_reviewed(
                    conn,
                    tenant_id=str(target_tenant_id),
                    request_id=str(request_id),
                    reviewer=op.principal_id,
                )
            except CommitmentError as e:
                refused = e
    raise _fail(refused)


@router.get("/v1/commercial/finance/eligibility/{target_tenant_id}")
async def commercial_eligibility(
    target_tenant_id: uuid.UUID, request: Request, op: OperatorDep
) -> dict[str, Any]:
    """O01: the classification, the nine gates and what blocks paid activation for one organization. Reading it never activates anything."""
    from zid_platform.modules.commercial.ports import NoTaxConfigured, RecordingPayments

    state = request.app.state
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _gate(conn, op)
        async with in_tenant(conn, str(target_tenant_id)):
            return await eligibility.check(
                conn,
                str(target_tenant_id),
                issuer=getattr(state, "commercial_issuing_entity", None),
                tax_configured=not isinstance(
                    getattr(state, "tax_port", NoTaxConfigured()), NoTaxConfigured
                ),
                payments_configured=not isinstance(
                    getattr(state, "payment_port", RecordingPayments()), RecordingPayments
                ),
            )


@router.get("/v1/commercial/finance/overview")
async def finance_overview(request: Request, op: OperatorDep) -> dict[str, Any]:
    """F01: the workload summary, the requests that wait on this person, and the exceptions that stop money moving."""
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _gate(conn, op)
        return await finance.overview(conn, op.principal_id)
