"""Operator routes for refunds, disputes, reconciliation and period close (ZID-UX-COM-009 F03/F04; slice L-14f, CR-058).

Like the finance routes, every one needs an approved, unexpired `commercial.finance.manage` grant (the operator's access to the console); who may do what is the
appointment table and the signed policy, decided in the services. The server picks the tier and the approvers. A refund is only ever sent by the worker, never by a
route; a dispute closes only on the processor's event; a period closes only with two different signatures on the digest that was read.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from zid_db import tenant_transaction
from zid_errors import ZidError

from zid_platform.api_auth import request_engine
from zid_platform.modules.commercial.ports import (
    FinanceError,
    OpsError,
    RecordingRefunds,
    bank_statement,
    disputes,
    recon,
    recon_import,
    refunds,
)
from zid_platform.modules.operator_access.api.commercial_admin import OperatorDep, _require
from zid_platform.modules.operator_access.api.commercial_finance import _env
from zid_platform.modules.operator_access.api.commercial_finance import _fail as _finance_fail

router = APIRouter(tags=["commercial-payment-ops"])

_NOT_FOUND = {"PAYMENT_NOT_FOUND", "REFUND_NOT_FOUND", "DISPUTE_NOT_FOUND", "EXCEPTION_NOT_FOUND"}
_VALIDATION = {
    "AMOUNT_INVALID",
    "CATEGORY_INVALID",
    "EVIDENCE_REQUIRED",
    "PERIOD_INVALID",
    "LEG_INVALID",
    "LEGS_INVALID",
    "RESOLUTION_REQUIRED",
    "REASON_REQUIRED",
    "EVIDENCE_ITEM_INVALID",
    "STRATEGY_INVALID",
}


def _fail(e: OpsError) -> ZidError:
    return ZidError(
        e.code,
        e.detail,
        http_status=404 if e.code in _NOT_FOUND else 422 if e.code in _VALIDATION else 409,
    )


def _refuse(e: Exception) -> ZidError:
    return _fail(e) if isinstance(e, OpsError) else _finance_fail(e)  # type: ignore[arg-type]


async def _gate(conn: Any, op: Any) -> None:
    await _require(conn, op, "commercial.finance.manage", None, "commercial:finance")


def _port(request: Request) -> Any:
    return getattr(request.app.state, "refund_port", None) or RecordingRefunds()


class RefundBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_tenant_id: uuid.UUID
    payment_attempt_id: uuid.UUID
    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    category: Literal[
        "DUPLICATE_PAYMENT", "CORRECTION", "CANCELLATION", "CONTRACT_RIGHT", "GOODWILL"
    ]
    reason: str = Field(min_length=1, max_length=500)
    evidence_ref: str | None = Field(default=None, max_length=200)
    credit_note_id: uuid.UUID | None = None


class RefundDecideBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: str = Field(min_length=2, max_length=40)
    decision: Literal["APPROVE", "REJECT"]
    snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    reason: str | None = Field(default=None, max_length=500)


class AssignBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    owner: uuid.UUID


class EvidenceBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    item: Literal[
        "terms_or_consent",
        "transaction_proof",
        "usage_proof",
        "communications",
        "cancellation_refund_chronology",
    ]
    ref: str = Field(min_length=1, max_length=300)


class DisputeDecideBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    strategy: Literal["ACCEPT", "CONTEST"]
    reason: str = Field(min_length=1, max_length=500)


class SubmissionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reference: str = Field(min_length=1, max_length=200)


class LegsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    legs: list[dict[str, Any]] = Field(min_length=1, max_length=5000)


class StatementBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    format: Literal["camt053"]
    content: str = Field(min_length=20, max_length=5_000_000)


class StripePayoutsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    payout_ids: list[str] = Field(min_length=1, max_length=20)


class SignCloseBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: str = Field(min_length=2, max_length=40)
    snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class ReopenBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=1, max_length=500)
    impact: str = Field(min_length=1, max_length=500)


class ReopenApproveBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: str = Field(min_length=2, max_length=40)


class ResolveBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    resolution: str = Field(min_length=1, max_length=500)


# ------------------------------------------------------------------------------------------------------------------------------ refunds


@router.get("/v1/commercial/payments/refunds")
async def list_refunds(
    request: Request,
    op: OperatorDep,
    state: Annotated[str | None, Query(max_length=30)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _gate(conn, op)
        return {"items": await refunds.list_cases(conn, state=state, limit=limit)}


@router.post("/v1/commercial/payments/refunds", status_code=201)
async def open_refund(body: RefundBody, request: Request, op: OperatorDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _gate(conn, op)
        try:
            return await refunds.open_refund(
                conn,
                tenant_id=str(body.target_tenant_id),
                payment_attempt_id=str(body.payment_attempt_id),
                amount=body.amount,
                category=body.category,
                reason=body.reason,
                requested_by=op.principal_id,
                evidence_ref=body.evidence_ref,
                credit_note_id=str(body.credit_note_id) if body.credit_note_id else None,
                environment=_env(request),
            )
        except (OpsError, FinanceError) as e:
            raise _refuse(e) from e


@router.get("/v1/commercial/payments/refunds/{refund_id}")
async def get_refund(refund_id: uuid.UUID, request: Request, op: OperatorDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _gate(conn, op)
        try:
            return await refunds.view(conn, str(refund_id))
        except (OpsError, FinanceError) as e:
            raise _refuse(e) from e


@router.post("/v1/commercial/payments/refunds/{refund_id}:decide")
async def decide_refund(
    refund_id: uuid.UUID, body: RefundDecideBody, request: Request, op: OperatorDep
) -> dict[str, Any]:
    refused: Exception | None = None
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _gate(conn, op)
        try:
            return await refunds.decide(
                conn,
                str(refund_id),
                role=body.role,
                approver=op.principal_id,
                decision=body.decision,
                presented_hash=body.snapshot_hash,
                reason=body.reason,
                environment=_env(request),
            )
        except (OpsError, FinanceError) as e:
            refused = e  # raised after the transaction ends, so a lapse written on the way is kept
    assert refused is not None
    raise _refuse(refused)


@router.post("/v1/commercial/payments/refunds/{refund_id}:execute")
async def execute_refund(refund_id: uuid.UUID, request: Request, op: OperatorDep) -> dict[str, Any]:
    """Queue an approved refund for the worker. Nothing is sent to the processor by this request."""
    refused: Exception | None = None
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _gate(conn, op)
        try:
            return await refunds.execute(
                conn, str(refund_id), executor=op.principal_id, environment=_env(request)
            )
        except (OpsError, FinanceError) as e:
            refused = e  # a voided approval written on the way is kept
    assert refused is not None
    raise _refuse(refused)


@router.post("/v1/commercial/payments/refunds/{refund_id}:cancel")
async def cancel_refund(refund_id: uuid.UUID, request: Request, op: OperatorDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _gate(conn, op)
        try:
            return await refunds.cancel(conn, str(refund_id), by=op.principal_id)
        except (OpsError, FinanceError) as e:
            raise _refuse(e) from e


# ---------------------------------------------------------------------------------------------------------------------------- disputes


@router.get("/v1/commercial/payments/disputes")
async def list_disputes(
    request: Request,
    op: OperatorDep,
    open_only: bool = False,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _gate(conn, op)
        return {"items": await disputes.list_cases(conn, open_only=open_only, limit=limit)}


@router.get("/v1/commercial/payments/disputes/{dispute_id}")
async def get_dispute(dispute_id: uuid.UUID, request: Request, op: OperatorDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _gate(conn, op)
        try:
            return await disputes.view(conn, str(dispute_id))
        except (OpsError, FinanceError) as e:
            raise _refuse(e) from e


@router.post("/v1/commercial/payments/disputes/{dispute_id}:assign")
async def assign_dispute(
    dispute_id: uuid.UUID, body: AssignBody, request: Request, op: OperatorDep
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _gate(conn, op)
        try:
            return await disputes.assign(
                conn, str(dispute_id), owner=str(body.owner), by=op.principal_id
            )
        except (OpsError, FinanceError) as e:
            raise _refuse(e) from e


@router.post("/v1/commercial/payments/disputes/{dispute_id}:evidence")
async def add_dispute_evidence(
    dispute_id: uuid.UUID, body: EvidenceBody, request: Request, op: OperatorDep
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _gate(conn, op)
        try:
            return await disputes.add_evidence(
                conn, str(dispute_id), item=body.item, ref=body.ref, by=op.principal_id
            )
        except (OpsError, FinanceError) as e:
            raise _refuse(e) from e


@router.post("/v1/commercial/payments/disputes/{dispute_id}:decide")
async def decide_dispute(
    dispute_id: uuid.UUID, body: DisputeDecideBody, request: Request, op: OperatorDep
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _gate(conn, op)
        try:
            return await disputes.decide(
                conn,
                str(dispute_id),
                strategy=body.strategy,
                reason=body.reason,
                by=op.principal_id,
                environment=_env(request),
            )
        except (OpsError, FinanceError) as e:
            raise _refuse(e) from e


@router.post("/v1/commercial/payments/disputes/{dispute_id}:record-submission")
async def record_dispute_submission(
    dispute_id: uuid.UUID, body: SubmissionBody, request: Request, op: OperatorDep
) -> dict[str, Any]:
    """Record that evidence was handed to the processor's dashboard. This is a record of a hand-off, never an outcome."""
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _gate(conn, op)
        try:
            return await disputes.record_submission(
                conn, str(dispute_id), reference=body.reference, by=op.principal_id
            )
        except (OpsError, FinanceError) as e:
            raise _refuse(e) from e


@router.post("/v1/commercial/payments/disputes/{dispute_id}:verify-submission")
async def verify_dispute_submission(
    dispute_id: uuid.UUID, request: Request, op: OperatorDep
) -> dict[str, Any]:
    """Ask the processor what it holds for this dispute. Only what it reports moves the case."""
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _gate(conn, op)
        try:
            return await disputes.verify_submission(conn, str(dispute_id), _port(request))
        except (OpsError, FinanceError) as e:
            raise _refuse(e) from e


# ------------------------------------------------------------------------------------------------------------------ reconciliation


@router.get("/v1/commercial/payments/reconciliation/{issuing_entity}/{period}")
async def reconciliation_board(
    issuing_entity: str, period: str, request: Request, op: OperatorDep
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _gate(conn, op)
        try:
            return await recon.board(conn, issuing_entity, period)
        except OpsError as e:
            raise _fail(e) from e


@router.post("/v1/commercial/payments/reconciliation/{issuing_entity}:import-legs")
async def import_recon_legs(
    issuing_entity: str, body: LegsBody, request: Request, op: OperatorDep
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _gate(conn, op)
        try:
            return await recon.import_legs(
                conn, issuing_entity=issuing_entity, legs=body.legs, by=op.principal_id
            )
        except (OpsError, FinanceError) as e:
            raise _refuse(e) from e


@router.post("/v1/commercial/payments/reconciliation/{issuing_entity}:import-bank-statement")
async def import_bank_statement(
    issuing_entity: str, body: StatementBody, request: Request, op: OperatorDep
) -> dict[str, Any]:
    """A bank statement in ISO 20022 camt.053 becomes BANK_LINE legs, keyed by the payout id its remittance text names. An entry that names none stays under its own bank reference
    and so stays unmatched: it is never guessed onto a payout."""
    try:
        legs, skipped = bank_statement.legs_from_camt053(body.content.encode("utf-8"))
    except recon_import.ImportError_ as e:
        raise ZidError(e.code, e.detail, http_status=422) from e
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _gate(conn, op)
        try:
            return {
                **await recon.import_legs(
                    conn, issuing_entity=issuing_entity, legs=legs, by=op.principal_id
                ),
                "unreadable_entries": skipped,
            }
        except (OpsError, FinanceError) as e:
            raise _refuse(e) from e


@router.post("/v1/commercial/payments/reconciliation/{issuing_entity}:import-stripe-payouts")
async def import_stripe_payouts(
    issuing_entity: str, body: StripePayoutsBody, request: Request, op: OperatorDep
) -> dict[str, Any]:
    """Ask the processor for each payout's own detail and import it as PAYOUT_LINE legs. The processor is asked here, and its answer is never filled in from the platform's records."""
    port = _port(request)
    legs: list[dict[str, Any]] = []
    skipped = 0
    for payout_id in dict.fromkeys(body.payout_ids):
        got = await port.payout_transactions(payout_id)
        if got is None:
            raise ZidError(
                "PAYOUT_UNAVAILABLE",
                f"the processor holds no payout {payout_id} or could not be reached",
                http_status=502,
                retryable=True,
            )
        try:
            more, bad = recon_import.legs_from_payout(*got)
        except recon_import.ImportError_ as e:
            raise ZidError(e.code, e.detail, http_status=422) from e
        legs += more
        skipped += bad
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _gate(conn, op)
        try:
            if not legs:
                return {"added": 0, "skipped": 0, "unreadable_transactions": skipped}
            return {
                **await recon.import_legs(
                    conn, issuing_entity=issuing_entity, legs=legs, by=op.principal_id
                ),
                "unreadable_transactions": skipped,
            }
        except (OpsError, FinanceError) as e:
            raise _refuse(e) from e


@router.get("/v1/commercial/payments/reconciliation/{issuing_entity}/{period}/exceptions")
async def recon_exceptions(
    issuing_entity: str, period: str, request: Request, op: OperatorDep
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _gate(conn, op)
        return {"items": await recon.exceptions(conn, issuing_entity, period)}


@router.post("/v1/commercial/payments/reconciliation/exceptions/{exception_id}:resolve")
async def resolve_recon_exception(
    exception_id: uuid.UUID, body: ResolveBody, request: Request, op: OperatorDep
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _gate(conn, op)
        try:
            return await recon.resolve_exception(
                conn, str(exception_id), resolution=body.resolution, by=op.principal_id
            )
        except (OpsError, FinanceError) as e:
            raise _refuse(e) from e


@router.post("/v1/commercial/payments/reconciliation/{issuing_entity}/{period}:prepare-close")
async def prepare_recon_close(
    issuing_entity: str, period: str, request: Request, op: OperatorDep
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _gate(conn, op)
        try:
            return await recon.prepare_close(conn, issuing_entity, period, by=op.principal_id)
        except (OpsError, FinanceError) as e:
            raise _refuse(e) from e


@router.post("/v1/commercial/payments/reconciliation/{issuing_entity}/{period}:sign-close")
async def sign_recon_close(
    issuing_entity: str, period: str, body: SignCloseBody, request: Request, op: OperatorDep
) -> dict[str, Any]:
    refused: Exception | None = None
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _gate(conn, op)
        try:
            return await recon.sign_close(
                conn,
                issuing_entity,
                period,
                role=body.role,
                signer=op.principal_id,
                presented_hash=body.snapshot_hash,
            )
        except (OpsError, FinanceError) as e:
            refused = e
    assert refused is not None
    raise _refuse(refused)


@router.post("/v1/commercial/payments/reconciliation/{issuing_entity}/{period}:request-reopen")
async def request_recon_reopen(
    issuing_entity: str, period: str, body: ReopenBody, request: Request, op: OperatorDep
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _gate(conn, op)
        try:
            return await recon.request_reopen(
                conn,
                issuing_entity,
                period,
                reason=body.reason,
                impact=body.impact,
                by=op.principal_id,
            )
        except (OpsError, FinanceError) as e:
            raise _refuse(e) from e


@router.post("/v1/commercial/payments/reconciliation/{issuing_entity}/{period}:approve-reopen")
async def approve_recon_reopen(
    issuing_entity: str, period: str, body: ReopenApproveBody, request: Request, op: OperatorDep
) -> dict[str, Any]:
    refused: Exception | None = None
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _gate(conn, op)
        try:
            return await recon.approve_reopen(
                conn, issuing_entity, period, role=body.role, approver=op.principal_id
            )
        except (OpsError, FinanceError) as e:
            refused = e
    assert refused is not None
    raise _refuse(refused)


@router.get("/v1/commercial/payments/reconciliation/{issuing_entity}/{period}/evidence-pack")
async def recon_evidence_pack(
    issuing_entity: str, period: str, request: Request, op: OperatorDep
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _gate(conn, op)
        try:
            return await recon.evidence_pack(conn, issuing_entity, period)
        except OpsError as e:
            raise _fail(e) from e
