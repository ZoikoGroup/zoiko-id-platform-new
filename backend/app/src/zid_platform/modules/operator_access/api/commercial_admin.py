"""Commercial operator surface (ZID-COM-001 §6, §12; CR-024, CR-025).

Lives with the operator plane because every route here is an operator action: the operator gate (operator
client, operator tenant, AAL-Z3) plus a live JIT grant. Keeping it here, not in the commercial module, keeps
the module dependency graph acyclic: commercial imports no other module.

    GET  /v1/commercial/price-books                       all versions, newest first
    GET  /v1/commercial/price-books/{id}                  one book with its lines
    GET  /v1/commercial/price-books/{id}/readiness        what it still lacks before it is approved
    POST /v1/commercial/price-books                       a new DRAFT, optionally a copy       (commercial.catalog.manage)
    PUT  /v1/commercial/price-books/{id}/lines            set one line of a DRAFT              (commercial.catalog.manage)
    POST /v1/commercial/price-books/{id}:approve          a different operator approves        (commercial.catalog.manage)
    GET  /v1/commercial/plan-requests                     every company's open plan requests
    POST /v1/commercial/plan-requests/{id}:answer         accept (we will raise the order) or decline, with a reason
    GET  /v1/commercial/service-requests                  every company's open requests for a contracted service
    POST /v1/commercial/service-requests/{id}:move        start it, finish it (ACTIVE) or decline it, with a note the company reads
    PUT  /v1/commercial/tenants/{tenant_id}/entitlement   move a tenant to a plan from now     (tenant.config.change)
    GET  /v1/commercial/tenants/{tenant_id}/enforcement   whether this company's plan limits are enforced or only reported
    PUT  /v1/commercial/tenants/{tenant_id}/enforcement   set it to ENFORCE or SHADOW, with a reason (tenant.config.change)
    POST /v1/commercial/tenants/{tenant_id}/overrides     a contracted addition, second approver
    POST /v1/commercial/tenants/{tenant_id}/data-deletion  delete a closed tenant's data (tenant.data.delete)
    GET  /v1/commercial/tenants/{tenant_id}/data-deletion  what was deleted, what is held, what is still owed
"""

from __future__ import annotations

import csv
import io
import re
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from zid_db import in_tenant, tenant_transaction
from zid_errors import ZidError

from zid_platform.api_auth import request_engine
from zid_platform.modules.commercial.ports import (
    PLAN_CODES,
    BillableError,
    CommercialError,
    Credit,
    FinanceError,
    InvoicingError,
    OrderError,
    PlanRequestError,
    QuoteLine,
    RatingError,
    ServiceRequestError,
    VendorError,
    assurance,
    billable_event_adjusted,
    billable_store,
    enforcement_store,
    entitlement_changed,
    family_registry,
    family_text_store,
    finance,
    freeze_month,
    fx_store,
    invoicing,
    ledger_summary,
    log_change,
    market_matrix_model,
    market_matrix_store,
    meter_window_frozen,
    next_month,
    open_payment_refund,
    operator_scope,
    orders,
    partner_model,
    partner_store,
    payment_event_queue,
    plan_requests,
    profitability_service,
    purchase_order_store,
    reconcile,
    resolve_payment_event,
    rollout_store,
    run_rating,
    service_requests,
    solution_store,
    vendors,
    verify_chain,
    verify_change_log,
)
from zid_platform.modules.commercial.ports import (
    activations as activations_service,
)
from zid_platform.modules.commercial.ports import (
    catalogue_store as store,
)
from zid_platform.modules.evidence.ports import current_runtime
from zid_platform.modules.operator_access.ports import (
    DIRECT_APPROVER,
    direct_mode,
    has_capability,
    is_active_operator,
    require_operator,
)

router = APIRouter(tags=["commercial"])
OperatorDep = Annotated[Any, Depends(require_operator)]
PlanCode = Literal["DEVELOPER", "LAUNCH", "BUSINESS", "SCALE", "ENTERPRISE"]

_STATUS = {
    "PRICE_BOOK_NOT_FOUND": 404,
    "PRICE_BOOK_NOT_DRAFT": 409,
    "PRICE_BOOK_SELF_APPROVAL": 403,
    "PRICE_BOOK_EMPTY": 422,
    "ENTITLEMENT_EFFECTIVE_DATE": 409,
    "OPERATOR_CAPABILITY_REQUIRED": 403,
    "MARKET_MATRIX_NOT_FOUND": 404,
    "MARKET_MATRIX_NOT_DRAFT": 409,
    "MARKET_MATRIX_SELF_APPROVAL": 403,
    "MARKET_MATRIX_ROLE_ALREADY_SIGNED": 409,
    "MARKET_MATRIX_PERSON_ALREADY_SIGNED": 409,
    "MARKET_MATRIX_APPROVALS_MISSING": 409,
    "MARKET_MATRIX_APPROVAL_REFUSED": 409,
    "MARKET_MATRIX_EMPTY": 422,
    "MARKET_MATRIX_ROLE_UNKNOWN": 422,
    "ROLLOUT_NOT_IN_ACTIVE_MATRIX": 404,
    "ROLLOUT_STAGE_UNKNOWN": 422,
    "PARTNER_NOT_FOUND": 404,
    "FAMILY_TEXT_NOT_FOUND": 404,
    "FAMILY_TEXT_NOT_DRAFT": 409,
    "FAMILY_TEXT_SELF_ACTIVATION": 403,
    "FAMILY_TEXT_REFUSED": 409,
    "FAMILY_UNKNOWN": 404,
    "NAME_LENGTH": 422,
    "UNIT_LENGTH": 422,
    "QUALIFIER_LENGTH": 422,
    "PRICE_IN_TEXT": 422,
    "CLAIM_IN_LABEL": 422,
    "FX_SET_NOT_FOUND": 404,
    "FX_SET_NOT_DRAFT": 409,
    "FX_SELF_ACTIVATION": 403,
    "FX_SET_REFUSED": 409,
    "FX_RATE_REFUSED": 422,
    "FX_ACTIVATION_REFUSED": 409,
    "PO_NUMBER_EXISTS": 409,
    "PO_NOT_OPEN": 409,
    "PO_REFUSED": 409,
    "PARTNER_NOT_PROPOSED": 409,
    "PARTNER_SELF_ACTIVATION": 403,
    "PARTNER_REFUSED": 409,
    "PARTNER_STATUS_REFUSED": 409,
    "PARTNER_TERMINATED": 409,
    "CHILD_IS_PARTNER": 422,
    "CHILD_ALREADY_IN_CHANNEL": 409,
    "CHILD_NOT_FOUND": 404,
    "SETTLEMENT_NOT_FOUND": 404,
    "SETTLEMENT_ALREADY_DECIDED": 409,
    "SETTLEMENT_SELF_APPROVAL": 403,
    "SETTLEMENT_REFUSED": 409,
    "ROLLOUT_NOT_STARTED": 409,
    "ROLLOUT_ALREADY_IN_STAGE": 409,
    "ROLLOUT_NOT_LIVE": 409,
    "ROLLOUT_AT_END": 409,
    "ROLLOUT_STAGE_SKIP": 409,
    "ROLLOUT_SAME_PERSON": 403,
}


def _fail(e: CommercialError) -> ZidError:
    return ZidError(
        e.code,
        e.detail or e.code.replace("_", " ").capitalize() + ".",
        http_status=_STATUS.get(e.code, 422),
    )


def _approver(op: Any) -> str:
    """Who is recorded as the second person: the operator in governed mode, the direct approver in direct mode (the
    real operator is the actor in the change log either way)."""
    return DIRECT_APPROVER if direct_mode() else str(op.principal_id)


def _iso(d: datetime | None) -> str | None:
    return d.isoformat() if d else None


def _book_view(b: dict[str, Any]) -> dict[str, Any]:
    return {
        "price_book_id": str(b["price_book_id"]),
        "version": b["version"],
        "currency": b["currency"],
        "status": b["status"],
        "effective_from": _iso(b["effective_from"]),
        "approved_at": _iso(b["approved_at"]),
        "note": b["note"],
    }


# ------------------------------------------------------------------------------- operator


class DraftBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    copy_of: uuid.UUID | None = None
    note: str | None = Field(default=None, max_length=500)


class LineBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sku: str = Field(pattern=r"^ZID-[A-Z0-9]{2,8}$")
    plan: PlanCode
    unit_rate: Decimal = Field(ge=0, max_digits=18, decimal_places=6)
    unit_divisor: int = Field(default=1, ge=1, le=1_000_000)
    included_quantity: int | None = Field(default=None, ge=0)
    minimum_amount: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=6)


class ApproveBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    effective_from: datetime | None = None


class PlanBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan: PlanCode


class OverrideBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    capability: str = Field(pattern=r"^[a-z][a-z0-9_]{1,40}$")
    grant_mode: Literal["INCLUDED", "ADD_ON", "METERED", "CONTRACT", "QUALIFIED"] | None = None
    extra_count: int | None = Field(default=None, ge=0, le=10_000)
    reason: str = Field(min_length=3, max_length=500)
    approver_id: uuid.UUID | None = None  # required in governed mode; direct mode needs none
    expires_at: datetime | None = None


async def _require(conn: Any, op: Any, capability: str, tenant: str | None, resource: str) -> None:
    ok = await has_capability(
        conn, op.principal_id, capability, tenant, resource, datetime.now(UTC)
    )
    if not ok:
        raise ZidError(
            "OPERATOR_CAPABILITY_REQUIRED",
            f"This needs an approved, unexpired {capability} grant.",
            http_status=403,
        )


@router.get("/v1/commercial/price-books")
async def list_books(request: Request, op: OperatorDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        return {"items": [_book_view(b) for b in await store.price_books(conn)]}


@router.get("/v1/commercial/price-books/{price_book_id}/readiness")
async def book_readiness(
    price_book_id: uuid.UUID, request: Request, op: OperatorDep
) -> dict[str, Any]:
    """What this price book still lacks before it is approved: unpriced SKU and plan pairs, and lines chosen as free."""
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        if await store.price_book(conn, str(price_book_id)) is None:
            raise _fail(CommercialError("PRICE_BOOK_NOT_FOUND"))
        return await store.readiness(conn, str(price_book_id))


@router.get("/v1/commercial/price-books/{price_book_id}")
async def get_book(price_book_id: uuid.UUID, request: Request, op: OperatorDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        b = await store.price_book(conn, str(price_book_id))
        if b is None:
            raise _fail(CommercialError("PRICE_BOOK_NOT_FOUND"))
        lines = await store.lines(conn, str(price_book_id))
    return {
        **_book_view(b),
        "lines": [
            {
                "sku": ln["sku_id"],
                "plan": ln["plan_code"],
                "unit_rate": format(ln["unit_rate"].normalize(), "f"),
                "unit_divisor": ln["unit_divisor"],
                "included_quantity": ln["included_quantity"],
                # a line edited in the console is written back whole, so the minimum must be read here or an edit would erase it
                "minimum_amount": format(ln["minimum_amount"].normalize(), "f")
                if ln["minimum_amount"] is not None
                else None,
                "unit": ln["unit"],
            }
            for ln in lines
        ],
    }


@router.post("/v1/commercial/price-books", status_code=201)
async def create_book(body: DraftBody, request: Request, op: OperatorDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _require(conn, op, "commercial.catalog.manage", None, "commercial:catalogue")
        try:
            return await store.create_draft(
                conn,
                copy_of=str(body.copy_of) if body.copy_of else None,
                created_by=op.principal_id,
                note=body.note,
            )
        except CommercialError as e:
            raise _fail(e) from e


@router.put("/v1/commercial/price-books/{price_book_id}/lines")
async def put_line(
    price_book_id: uuid.UUID, body: LineBody, request: Request, op: OperatorDep
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _require(conn, op, "commercial.catalog.manage", None, "commercial:catalogue")
        b = await store.price_book(conn, str(price_book_id))
        if b is None:
            raise _fail(CommercialError("PRICE_BOOK_NOT_FOUND"))
        if b["status"] != "DRAFT":
            raise _fail(
                CommercialError("PRICE_BOOK_NOT_DRAFT", "an approved price book is immutable")
            )
        await store.put_line(
            conn,
            str(price_book_id),
            sku_id=body.sku,
            plan_code=body.plan,
            unit_rate=body.unit_rate,
            unit_divisor=body.unit_divisor,
            included_quantity=body.included_quantity,
            minimum_amount=body.minimum_amount,
        )
    return {"price_book_id": str(price_book_id), "sku": body.sku, "plan": body.plan}


@router.post("/v1/commercial/price-books/{price_book_id}:approve")
async def approve_book(
    price_book_id: uuid.UUID, body: ApproveBody, request: Request, op: OperatorDep
) -> dict[str, Any]:
    now = datetime.now(UTC)
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _require(conn, op, "commercial.catalog.manage", None, "commercial:catalogue")
        try:
            await store.approve(
                conn,
                str(price_book_id),
                approver=_approver(op),
                effective_from=body.effective_from or now,
                now=now,
            )
            await log_change(
                conn,
                kind="PRICE_BOOK_APPROVED",
                ref=str(price_book_id),
                tenant_id=None,
                actor=op.principal_id,
                detail={"effective_from": (body.effective_from or now).isoformat()},
            )
        except CommercialError as e:
            raise _fail(e) from e
        b = await store.price_book(conn, str(price_book_id))
    assert b is not None
    return _book_view(b)


# --------------------------------------------------------------------------------------------- the market matrix (F06 / O01)


class MatrixRowBody(BaseModel):
    """One (family, country) row. Every field is a recorded statement or a reference to signed evidence: this API cannot make a reference true."""

    model_config = ConfigDict(extra="forbid")
    family: str = Field(pattern=r"^[A-Z]{2,8}$")
    country: str = Field(pattern=r"^[A-Z]{2}$")
    seller_entity: str = Field(min_length=1, max_length=160)
    merchant_route: str = Field(min_length=1, max_length=160)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    tax_class: str = Field(min_length=1, max_length=80)
    data_region: str = Field(min_length=1, max_length=40)
    offer_ref: str = Field(min_length=1, max_length=255)
    legal_basis_ref: str = Field(min_length=1, max_length=255)
    release_evidence_ref: str = Field(min_length=1, max_length=255)
    payment_configured: bool
    tax_configured: bool
    provider_rights_ref: str | None = Field(default=None, max_length=255)
    regulated_status_ref: str | None = Field(default=None, max_length=255)
    provider_rights_expires: date | None = None
    regulated_status_expires: date | None = None


class MatrixApproveBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["PRODUCT", "FINANCE", "LEGAL_PRIVACY", "SECURITY_QA"]


def _matrix_view(m: dict[str, Any]) -> dict[str, Any]:
    return {
        "matrix_id": str(m["matrix_id"]),
        "version": m["version"],
        "status": m["status"],
        "effective_from": _iso(m["effective_from"]),
        "activated_at": _iso(m["activated_at"]),
        "note": m["note"],
    }


def _matrix_row_view(r: Any) -> dict[str, Any]:
    return {k: getattr(r, k) for k in r.__slots__}


@router.get("/v1/commercial/market-matrices")
async def list_matrices(request: Request, op: OperatorDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        return {"items": [_matrix_view(m) for m in await market_matrix_store.matrices(conn)]}


class RolloutBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    family: str = Field(pattern=r"^[A-Z]{2,8}$")
    country: str = Field(pattern=r"^[A-Z]{2}$")
    stage: Literal["SANDBOX", "STAGING", "PILOT", "CANARY", "PRODUCTION", "PAUSED", "ROLLED_BACK"]
    reason: str = Field(min_length=3, max_length=500)


@router.get("/v1/commercial/market-rollout")
async def list_rollout(request: Request, op: OperatorDep) -> dict[str, Any]:
    """Where every market of the active matrix is in its staged rollout, and who made the latest move."""
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        _, rows = await market_matrix_store.active_rows(conn, datetime.now(UTC))
        latest = await rollout_store.current(conn)
    return {
        "items": [
            {
                "family": f,
                "country": c,
                "stage": latest.get((f, c), {}).get("stage"),
                "decided_by": str(latest[(f, c)]["decided_by"]) if (f, c) in latest else None,
                "decided_at": _iso(latest[(f, c)]["decided_at"]) if (f, c) in latest else None,
            }
            for (f, c) in sorted(rows)
        ]
    }


@router.post("/v1/commercial/market-rollout", status_code=201)
async def move_rollout(body: RolloutBody, request: Request, op: OperatorDep) -> dict[str, Any]:
    """Move one market of the active matrix one stage on, pause it, or roll it back. Advancing needs a different person than the last move; stopping never does."""
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _require(conn, op, "commercial.catalog.manage", None, "commercial:catalogue")
        try:
            out = await rollout_store.move(
                conn,
                family=body.family,
                country=body.country,
                target=body.stage,
                decider=str(op.principal_id),
                reason=body.reason,
            )
            await log_change(
                conn,
                kind="MARKET_ROLLOUT_MOVED",
                ref=f"{body.family}:{body.country}",
                tenant_id=None,
                actor=op.principal_id,
                detail={"stage": body.stage},
            )
        except CommercialError as e:
            raise _fail(e) from e
    return out


class PartnerBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=2, max_length=160)
    kind: Literal["RESELLER", "REFERRAL", "EMBEDDED_ISV"]
    parent_tenant_id: uuid.UUID
    bill_to: Literal["PARTNER", "CHILD"]
    territory: list[Annotated[str, Field(pattern=r"^[A-Z]{2}$")]] = Field(
        min_length=1, max_length=250
    )
    agreement_ref: str = Field(min_length=1, max_length=255)
    brand_terms_ref: str | None = Field(default=None, max_length=255)
    settlement_ref: str | None = Field(default=None, max_length=255)
    effective_until: datetime | None = None


class PartnerStatusBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["ACTIVE", "SUSPENDED", "TERMINATED"]


class PartnerChildBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    child_tenant_id: uuid.UUID
    downstream_consent_ref: str | None = Field(default=None, max_length=255)
    usage_visible: bool = False


class SettlementBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    period: date
    kind: Literal["COMMISSION", "BILLING_REPORT", "CREDIT_NOTE"]
    amount: Decimal = Field(gt=0, max_digits=18, decimal_places=6)
    currency: str = Field(pattern=r"^[A-Z]{3}$")


class SettlementDecisionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approve: bool
    reason: str = Field(min_length=3, max_length=500)


def _partner_view(p: dict[str, Any]) -> dict[str, Any]:
    return {
        **{k: (str(v) if isinstance(v, uuid.UUID) else v) for k, v in p.items()},
        "territory": list(p["territory"]),
        "effective_from": _iso(p["effective_from"]),
        "effective_until": _iso(p["effective_until"]),
        "created_at": _iso(p["created_at"]),
        "activated_at": _iso(p["activated_at"]),
    }


@router.get("/v1/commercial/partners")
async def list_partners(request: Request, op: OperatorDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        return {"items": [_partner_view(p) for p in await partner_store.partners(conn)]}


@router.get("/v1/commercial/partners/{partner_id}")
async def get_partner(partner_id: uuid.UUID, request: Request, op: OperatorDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        p = await partner_store.get(conn, str(partner_id))
        if p is None:
            raise _fail(CommercialError("PARTNER_NOT_FOUND"))
        kids = await partner_store.children(conn, str(partner_id))
    return {
        **_partner_view(p),
        "children": [
            {
                "child_tenant_id": str(c["child_tenant_id"]),
                "downstream_consent_ref": c["downstream_consent_ref"],
                "usage_visible": c["usage_visible"],
                "revoked_at": _iso(c["revoked_at"]),
            }
            for c in kids
        ],
    }


@router.post("/v1/commercial/partners", status_code=201)
async def create_partner(body: PartnerBody, request: Request, op: OperatorDep) -> dict[str, Any]:
    """Record a partner as PROPOSED, with a reference to its signed channel agreement. Another person activates it; nothing is sold or paid until then."""
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _require(conn, op, "commercial.catalog.manage", None, "commercial:catalogue")
        try:
            p = await partner_store.create(
                conn,
                name=body.name,
                kind=body.kind,
                parent_tenant_id=str(body.parent_tenant_id),
                bill_to=body.bill_to,
                territory=body.territory,
                agreement_ref=body.agreement_ref,
                brand_terms_ref=body.brand_terms_ref,
                settlement_ref=body.settlement_ref,
                effective_from=None,
                effective_until=body.effective_until,
                created_by=str(op.principal_id),
            )
        except CommercialError as e:
            raise _fail(e) from e
    return _partner_view(p)


@router.post("/v1/commercial/partners/{partner_id}:activate")
async def activate_partner(
    partner_id: uuid.UUID, body: ApproveBody, request: Request, op: OperatorDep
) -> dict[str, Any]:
    now = datetime.now(UTC)
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _require(conn, op, "commercial.catalog.manage", None, "commercial:catalogue")
        try:
            await partner_store.activate(
                conn,
                str(partner_id),
                activator=str(op.principal_id),
                effective_from=body.effective_from or now,
                now=now,
            )
            await log_change(
                conn,
                kind="PARTNER_ACTIVATED",
                ref=str(partner_id),
                tenant_id=None,
                actor=op.principal_id,
                detail={},
            )
        except CommercialError as e:
            raise _fail(e) from e
    return {"partner_id": str(partner_id), "status": "ACTIVE"}


@router.post("/v1/commercial/partners/{partner_id}:status")
async def set_partner_status(
    partner_id: uuid.UUID, body: PartnerStatusBody, request: Request, op: OperatorDep
) -> dict[str, Any]:
    """Suspend, resume or terminate. New operations stop at once; evidence already recorded is kept."""
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _require(conn, op, "commercial.catalog.manage", None, "commercial:catalogue")
        try:
            await partner_store.set_status(conn, str(partner_id), body.status)
            await log_change(
                conn,
                kind="PARTNER_STATUS",
                ref=str(partner_id),
                tenant_id=None,
                actor=op.principal_id,
                detail={"status": body.status},
            )
        except CommercialError as e:
            raise _fail(e) from e
    return {"partner_id": str(partner_id), "status": body.status}


@router.put("/v1/commercial/partners/{partner_id}/children", status_code=201)
async def add_partner_child(
    partner_id: uuid.UUID, body: PartnerChildBody, request: Request, op: OperatorDep
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _require(conn, op, "commercial.catalog.manage", None, "commercial:catalogue")
        try:
            await partner_store.add_child(
                conn,
                str(partner_id),
                child_tenant_id=str(body.child_tenant_id),
                permitted_by=str(op.principal_id),
                downstream_consent_ref=body.downstream_consent_ref,
                usage_visible=body.usage_visible,
            )
        except CommercialError as e:
            raise _fail(e) from e
    return {"partner_id": str(partner_id), "child_tenant_id": str(body.child_tenant_id)}


@router.post("/v1/commercial/partners/{partner_id}/children/{child_tenant_id}:revoke")
async def revoke_partner_child(
    partner_id: uuid.UUID, child_tenant_id: uuid.UUID, request: Request, op: OperatorDep
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _require(conn, op, "commercial.catalog.manage", None, "commercial:catalogue")
        try:
            await partner_store.revoke_child(conn, str(partner_id), str(child_tenant_id))
        except CommercialError as e:
            raise _fail(e) from e
    return {"partner_id": str(partner_id), "child_tenant_id": str(child_tenant_id), "revoked": True}


@router.get("/v1/commercial/partners/{partner_id}/usage")
async def partner_usage(
    partner_id: uuid.UUID,
    request: Request,
    op: OperatorDep,
    month: str = Query(pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
) -> dict[str, Any]:
    """The usage attributed to a partner in a month, reconciled once: every event carries the one payer the agreement names, and none is counted twice."""
    first = date(int(month[:4]), int(month[5:7]), 1)
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        p = await partner_store.get(conn, str(partner_id))
        if p is None:
            raise _fail(CommercialError("PARTNER_NOT_FOUND"))
        events = await partner_store.attributed_events(conn, str(partner_id), first)
    result = partner_model.reconcile(events, partner_store.model(p))
    by_child: dict[str, dict[str, str]] = {}
    for e in events:
        meters = by_child.setdefault(str(e["tenant_id"]), {})
        meters[e["meter_id"]] = str(
            Decimal(meters.get(e["meter_id"], "0")) + Decimal(e["quantity"])
        )
    return {"partner_id": str(partner_id), "month": month, **result, "by_child": by_child}


@router.post("/v1/commercial/partners/{partner_id}/settlements", status_code=201)
async def propose_partner_settlement(
    partner_id: uuid.UUID, body: SettlementBody, request: Request, op: OperatorDep
) -> dict[str, Any]:
    """Propose a commission, a billing report or a credit note for a partner. Only where a signed settlement arrangement is on file; a second person decides it. Nothing is paid by this."""
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _require(conn, op, "commercial.catalog.manage", None, "commercial:catalogue")
        try:
            sid = await partner_store.propose_settlement(
                conn,
                str(partner_id),
                period=body.period,
                kind=body.kind,
                amount=str(body.amount),
                currency=body.currency,
                proposed_by=str(op.principal_id),
            )
        except CommercialError as e:
            raise _fail(e) from e
    return {"settlement_id": sid, "status": "PROPOSED"}


@router.post("/v1/commercial/partner-settlements/{settlement_id}:decide")
async def decide_partner_settlement(
    settlement_id: uuid.UUID, body: SettlementDecisionBody, request: Request, op: OperatorDep
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _require(conn, op, "commercial.catalog.manage", None, "commercial:catalogue")
        try:
            await partner_store.decide_settlement(
                conn,
                str(settlement_id),
                decider=str(op.principal_id),
                approve=body.approve,
                reason=body.reason,
            )
            await log_change(
                conn,
                kind="PARTNER_SETTLEMENT_DECIDED",
                ref=str(settlement_id),
                tenant_id=None,
                actor=op.principal_id,
                detail={"approve": body.approve},
            )
        except CommercialError as e:
            raise _fail(e) from e
    return {
        "settlement_id": str(settlement_id),
        "status": "APPROVED" if body.approve else "REJECTED",
    }


class PurchaseOrderBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    po_number: str = Field(min_length=1, max_length=80)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    value: Decimal = Field(gt=0, max_digits=18, decimal_places=6)
    valid_from: date
    valid_until: date


def _po_view(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "po_id": str(r["po_id"]),
        "po_number": r["po_number"],
        "currency": r["currency"],
        "value": str(r["value"]),
        "drawn": str(r["drawn"]),
        "remaining": str(Decimal(r["value"]) - Decimal(r["drawn"])),
        "valid_from": r["valid_from"].isoformat(),
        "valid_until": r["valid_until"].isoformat(),
        "status": r["status"],
    }


@router.get("/v1/commercial/tenants/{tenant_id}/purchase-orders")
async def list_purchase_orders(
    tenant_id: uuid.UUID, request: Request, op: OperatorDep
) -> dict[str, Any]:
    tenant = str(tenant_id)
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _require(conn, op, "commercial.billing.manage", tenant, f"tenant:{tenant}")
        return {
            "items": [_po_view(r) for r in await purchase_order_store.operator_list(conn, tenant)]
        }


@router.post("/v1/commercial/tenants/{tenant_id}/purchase-orders", status_code=201)
async def record_purchase_order(
    tenant_id: uuid.UUID, body: PurchaseOrderBody, request: Request, op: OperatorDep
) -> dict[str, Any]:
    """Record a purchase order the company supplied. From then on its invoices are issued only against a PO that fits."""
    tenant = str(tenant_id)
    if body.valid_until < body.valid_from:
        raise ZidError(
            "PO_VALIDITY_INVALID", "A purchase order cannot end before it starts.", http_status=422
        )
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _require(conn, op, "commercial.billing.manage", tenant, f"tenant:{tenant}")
        async with in_tenant(conn, tenant):
            try:
                po_id = await purchase_order_store.record(
                    conn,
                    tenant_id=tenant,
                    po_number=body.po_number,
                    currency=body.currency,
                    value=str(body.value),
                    valid_from=body.valid_from,
                    valid_until=body.valid_until,
                    created_by=str(op.principal_id),
                )
                await log_change(
                    conn,
                    kind="PURCHASE_ORDER_RECORDED",
                    ref=po_id,
                    tenant_id=tenant,
                    actor=op.principal_id,
                    detail={"po_number": body.po_number, "currency": body.currency},
                )
            except CommercialError as e:
                raise _fail(e) from e
    return {"po_id": po_id}


@router.post("/v1/commercial/tenants/{tenant_id}/purchase-orders/{po_id}:close")
async def close_purchase_order(
    tenant_id: uuid.UUID, po_id: uuid.UUID, request: Request, op: OperatorDep
) -> dict[str, Any]:
    tenant = str(tenant_id)
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _require(conn, op, "commercial.billing.manage", tenant, f"tenant:{tenant}")
        async with in_tenant(conn, tenant):
            try:
                await purchase_order_store.close(conn, str(po_id))
            except CommercialError as e:
                raise _fail(e) from e
    return {"po_id": str(po_id), "status": "CLOSED"}


@router.get("/v1/commercial/profitability")
async def profitability(
    request: Request,
    op: OperatorDep,
    from_: str = Query(alias="from", pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    to: str = Query(pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
) -> dict[str, Any]:
    """Margin by SKU (rated revenue less provider cost in the same currency), run-rate and revenue retention over a range of months. Read only; finance authority."""
    start, end = date(int(from_[:4]), int(from_[5:7]), 1), date(int(to[:4]), int(to[5:7]), 1)
    if end < start or (end.year - start.year) * 12 + end.month - start.month > 24:
        raise ZidError(
            "RANGE_INVALID",
            "The range must run forwards and cover at most 25 months.",
            http_status=422,
        )
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _require(conn, op, "commercial.billing.manage", None, "commercial:finance")
        out: dict[str, Any] = await profitability_service.report(
            conn, start=start, end=end, as_of=datetime.now(UTC).date()
        )
        return out


class FxSetBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_ref: str = Field(min_length=3, max_length=255)
    as_of: date


class FxRateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    usd_per_unit: Decimal = Field(gt=0, max_digits=24, decimal_places=12)


@router.get("/v1/commercial/fx-rate-sets")
async def list_fx_sets(request: Request, op: OperatorDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        out = []
        for s in await fx_store.sets(conn):
            out.append(
                {
                    "set_id": str(s["set_id"]),
                    "version": s["version"],
                    "status": s["status"],
                    "source_ref": s["source_ref"],
                    "as_of": s["as_of"].isoformat(),
                    "rates": {
                        k: str(v) for k, v in (await fx_store.rates(conn, str(s["set_id"]))).items()
                    },
                }
            )
    return {"items": out}


@router.post("/v1/commercial/fx-rate-sets", status_code=201)
async def create_fx_set(body: FxSetBody, request: Request, op: OperatorDep) -> dict[str, Any]:
    """A draft rate set with its named source and as-of date. There are no default rates: the owner supplies each one from the named source."""
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _require(conn, op, "commercial.catalog.manage", None, "commercial:finance")
        try:
            return await fx_store.create_draft(
                conn, source_ref=body.source_ref, as_of=body.as_of, created_by=str(op.principal_id)
            )
        except CommercialError as e:
            raise _fail(e) from e


@router.put("/v1/commercial/fx-rate-sets/{set_id}/rates")
async def put_fx_rate(
    set_id: uuid.UUID, body: FxRateBody, request: Request, op: OperatorDep
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _require(conn, op, "commercial.catalog.manage", None, "commercial:finance")
        try:
            await fx_store.put_rate(conn, str(set_id), body.currency, str(body.usd_per_unit))
        except CommercialError as e:
            raise _fail(e) from e
    return {"set_id": str(set_id), "currency": body.currency}


@router.post("/v1/commercial/fx-rate-sets/{set_id}:activate")
async def activate_fx_set(set_id: uuid.UUID, request: Request, op: OperatorDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _require(conn, op, "commercial.catalog.manage", None, "commercial:finance")
        try:
            await fx_store.activate(
                conn, str(set_id), activator=str(op.principal_id), now=datetime.now(UTC)
            )
            await log_change(
                conn,
                kind="FX_RATE_SET_ACTIVATED",
                ref=str(set_id),
                tenant_id=None,
                actor=op.principal_id,
                detail={},
            )
        except CommercialError as e:
            raise _fail(e) from e
    return {"set_id": str(set_id), "status": "ACTIVE"}


@router.post("/v1/commercial/fx-rate-sets/{set_id}:withdraw")
async def withdraw_fx_set(set_id: uuid.UUID, request: Request, op: OperatorDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _require(conn, op, "commercial.catalog.manage", None, "commercial:finance")
        try:
            await fx_store.withdraw(conn, str(set_id))
        except CommercialError as e:
            raise _fail(e) from e
    return {"set_id": str(set_id), "status": "WITHDRAWN"}


class FamilyTextBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    family: str = Field(pattern=r"^[A-Z]{2,8}$")
    name: str = Field(min_length=2, max_length=80)
    unit: str = Field(min_length=2, max_length=120)
    sales_qualifier: str = Field(default="", max_length=300)
    reason: str = Field(min_length=3, max_length=500)


@router.get("/v1/commercial/service-family-texts")
async def list_family_texts(
    request: Request,
    op: OperatorDep,
    family: str | None = Query(default=None, pattern=r"^[A-Z]{2,8}$"),
) -> dict[str, Any]:
    """Every wording revision (newest first) beside the reviewed file's wording, so a reviewer sees what would change."""
    base = family_registry()
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        revs = await family_text_store.revisions(conn, family)
    return {
        "baseline": {
            f.code: {"name": f.name, "unit": f.unit, "sales_qualifier": f.sales_qualifier}
            for f in base.families.values()
            if family in (None, f.code)
        },
        "items": [
            {
                **{k: (str(v) if isinstance(v, uuid.UUID) else v) for k, v in r.items()},
                "created_at": _iso(r["created_at"]),
                "activated_at": _iso(r["activated_at"]),
            }
            for r in revs
        ],
    }


@router.post("/v1/commercial/service-family-texts", status_code=201)
async def propose_family_text(
    body: FamilyTextBody, request: Request, op: OperatorDep
) -> dict[str, Any]:
    """Propose new wording for one family. It changes nothing until a different person activates it, and it can never change what the family needs or does."""
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _require(conn, op, "commercial.catalog.manage", None, "commercial:catalogue")
        try:
            return await family_text_store.create_draft(
                conn,
                family=body.family,
                name=body.name,
                unit=body.unit,
                sales_qualifier=body.sales_qualifier,
                reason=body.reason,
                created_by=str(op.principal_id),
            )
        except CommercialError as e:
            raise _fail(e) from e


@router.post("/v1/commercial/service-family-texts/{rev_id}:activate")
async def activate_family_text(
    rev_id: uuid.UUID, request: Request, op: OperatorDep
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _require(conn, op, "commercial.catalog.manage", None, "commercial:catalogue")
        try:
            await family_text_store.activate(
                conn, str(rev_id), activator=str(op.principal_id), now=datetime.now(UTC)
            )
            await log_change(
                conn,
                kind="FAMILY_TEXT_ACTIVATED",
                ref=str(rev_id),
                tenant_id=None,
                actor=op.principal_id,
                detail={},
            )
        except CommercialError as e:
            raise _fail(e) from e
    return {"rev_id": str(rev_id), "status": "ACTIVE"}


@router.post("/v1/commercial/service-family-texts/{rev_id}:withdraw")
async def withdraw_family_text(
    rev_id: uuid.UUID, request: Request, op: OperatorDep
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _require(conn, op, "commercial.catalog.manage", None, "commercial:catalogue")
        try:
            await family_text_store.withdraw(conn, str(rev_id))
        except CommercialError as e:
            raise _fail(e) from e
    return {"rev_id": str(rev_id), "status": "WITHDRAWN"}


@router.get("/v1/commercial/quote-requests")
async def list_quote_requests(request: Request, op: OperatorDep) -> dict[str, Any]:
    """The deal desk's queue: every company's saved configuration that was sent for a quote, with the scope, assumptions and market exceptions frozen at the time."""
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _require(conn, op, "commercial.catalog.manage", None, "commercial:catalogue")
        return {"items": await solution_store.operator_requests(conn)}


@router.get("/v1/commercial/market-matrices/{matrix_id}")
async def get_matrix(matrix_id: uuid.UUID, request: Request, op: OperatorDep) -> dict[str, Any]:
    """One matrix with its rows, who has signed which role, and the roles still missing before it can be activated."""
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        m = await market_matrix_store.matrix(conn, str(matrix_id))
        if m is None:
            raise _fail(CommercialError("MARKET_MATRIX_NOT_FOUND"))
        rows = await market_matrix_store.rows(conn, str(matrix_id))
        signed = await market_matrix_store.approvals(conn, str(matrix_id))
    registry = family_registry()
    return {
        **_matrix_view(m),
        "rows": [_matrix_row_view(r) for r in rows],
        "incomplete_rows": [
            {
                "family": r.family,
                "country": r.country,
                "problems": market_matrix_model.row_problems(r, registry),
            }
            for r in rows
            if market_matrix_model.row_problems(r, registry)
        ],
        "rights_alerts": market_matrix_model.rights_alerts(rows, datetime.now(UTC).date()),
        "approvals": [
            {
                "role": a["role"],
                "approver": str(a["approver"]),
                "approved_at": _iso(a["approved_at"]),
            }
            for a in signed
        ],
        "missing_approvals": list(
            market_matrix_model.missing_approvals([a["role"] for a in signed])
        ),
    }


@router.post("/v1/commercial/market-matrices", status_code=201)
async def create_matrix(body: DraftBody, request: Request, op: OperatorDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _require(conn, op, "commercial.catalog.manage", None, "commercial:catalogue")
        try:
            return await market_matrix_store.create_draft(
                conn,
                copy_of=str(body.copy_of) if body.copy_of else None,
                created_by=str(op.principal_id),
                note=body.note,
            )
        except CommercialError as e:
            raise _fail(e) from e


@router.put("/v1/commercial/market-matrices/{matrix_id}/rows")
async def put_matrix_row(
    matrix_id: uuid.UUID, body: MatrixRowBody, request: Request, op: OperatorDep
) -> dict[str, Any]:
    row = market_matrix_model.Row(**body.model_dump())
    try:
        market_matrix_model.check_row(row, family_registry())
    except market_matrix_model.MatrixError as e:
        raise ZidError(e.code, f"This row is incomplete: {e.detail}.", http_status=422) from e
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _require(conn, op, "commercial.catalog.manage", None, "commercial:catalogue")
        try:
            await market_matrix_store.put_row(conn, str(matrix_id), row)
        except CommercialError as e:
            raise _fail(e) from e
    return {"matrix_id": str(matrix_id), "family": row.family, "country": row.country}


@router.post("/v1/commercial/market-matrices/{matrix_id}:approve")
async def approve_matrix(
    matrix_id: uuid.UUID, body: MatrixApproveBody, request: Request, op: OperatorDep
) -> dict[str, Any]:
    """One of four independent roles signs a DRAFT matrix. The author cannot, and one person cannot sign for two roles."""
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _require(conn, op, "commercial.catalog.manage", None, "commercial:catalogue")
        try:
            await market_matrix_store.approve(
                conn, str(matrix_id), role=body.role, approver=str(op.principal_id)
            )
            await log_change(
                conn,
                kind="MARKET_MATRIX_APPROVED",
                ref=str(matrix_id),
                tenant_id=None,
                actor=op.principal_id,
                detail={"role": body.role},
            )
            signed = await market_matrix_store.approvals(conn, str(matrix_id))
        except CommercialError as e:
            raise _fail(e) from e
    return {
        "matrix_id": str(matrix_id),
        "signed": [a["role"] for a in signed],
        "missing": list(market_matrix_model.missing_approvals([a["role"] for a in signed])),
    }


@router.post("/v1/commercial/market-matrices/{matrix_id}:activate")
async def activate_matrix(
    matrix_id: uuid.UUID, body: ApproveBody, request: Request, op: OperatorDep
) -> dict[str, Any]:
    """Make a fully approved matrix the one in force. Needs all four approvals, at least one complete row, and someone other than the author."""
    now = datetime.now(UTC)
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _require(conn, op, "commercial.catalog.manage", None, "commercial:catalogue")
        registry = family_registry()
        bad = [
            r
            for r in await market_matrix_store.rows(conn, str(matrix_id))
            if market_matrix_model.row_problems(r, registry)
        ]
        if bad:
            raise ZidError(
                "MARKET_MATRIX_ROW_INCOMPLETE",
                f"{len(bad)} row(s) are incomplete; fix them before activation.",
                http_status=422,
            )
        try:
            await market_matrix_store.activate(
                conn,
                str(matrix_id),
                activator=str(op.principal_id),
                effective_from=body.effective_from or now,
                now=now,
            )
            await log_change(
                conn,
                kind="MARKET_MATRIX_ACTIVATED",
                ref=str(matrix_id),
                tenant_id=None,
                actor=op.principal_id,
                detail={"effective_from": (body.effective_from or now).isoformat()},
            )
        except CommercialError as e:
            raise _fail(e) from e
        m = await market_matrix_store.matrix(conn, str(matrix_id))
    assert m is not None
    return _matrix_view(m)


@router.post("/v1/commercial/market-matrices/{matrix_id}:withdraw")
async def withdraw_matrix(
    matrix_id: uuid.UUID, request: Request, op: OperatorDep
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _require(conn, op, "commercial.catalog.manage", None, "commercial:catalogue")
        try:
            await market_matrix_store.withdraw(conn, str(matrix_id))
            await log_change(
                conn,
                kind="MARKET_MATRIX_WITHDRAWN",
                ref=str(matrix_id),
                tenant_id=None,
                actor=op.principal_id,
                detail={},
            )
        except CommercialError as e:
            raise _fail(e) from e
    return {"matrix_id": str(matrix_id), "status": "WITHDRAWN"}


class PlanRequestAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["ACCEPTED", "DECLINED"]
    note: str | None = Field(default=None, max_length=1000)


_PLAN_REQUEST_STATUS = {"NOT_FOUND": 404, "PLAN_REQUEST_CLOSED": 409, "VALIDATION_FAILED": 422}


@router.get("/v1/commercial/plan-requests")
async def open_plan_requests(request: Request, op: OperatorDep) -> dict[str, Any]:
    """Every company's open requests for a different plan, oldest first (CR-032)."""
    async with (
        tenant_transaction(request_engine(request), request.state.operator_ctx) as conn,
        operator_scope(conn),
    ):
        return {"items": await plan_requests.open_requests(conn)}


async def notify_plan_answer(conn: Any, answered: dict[str, Any]) -> None:
    """Tell the company, in its own Alerts, that its request was answered (the email for a plan change itself follows
    the entitlement event). Raised in the company's tenant context, never the operator's."""
    from zid_platform.modules.notification.ports import AlertDraft, Severity, raise_alert

    accepted = answered["status"] == "ACCEPTED"
    plan = str(answered["plan"]).title()
    note = answered.get("note") or ""
    draft = AlertDraft(
        severity=Severity.INFO,
        kind="commercial.plan_request.answered",
        title=f"Your request for the {plan} plan was {'accepted' if accepted else 'declined'}",
        body=note,
        object_type="plan_request",
        object_ref=answered["request_id"],
        href="/admin/billing",
        recommended_action="Open Billing to see your plan."
        if accepted
        else "You can ask for a different plan from Billing.",
        is_task=False,
        mandatory=False,
        correlation_key=f"plan_request.answered:{answered['request_id']}",
    )
    async with in_tenant(conn, answered["tenant_id"]):
        await raise_alert(conn, answered["tenant_id"], draft, now=datetime.now(UTC))


@router.post("/v1/commercial/plan-requests/{request_id}:answer")
async def answer_plan_request(
    request_id: uuid.UUID, body: PlanRequestAnswer, request: Request, op: OperatorDep
) -> dict[str, Any]:
    """ACCEPTED: the team will raise the order (the plan itself still moves only by a signed order or the plan change
    below). DECLINED: the company is told why. The company's own entitlement is untouched either way."""
    async with (
        tenant_transaction(request_engine(request), request.state.operator_ctx) as conn,
        operator_scope(conn),
    ):
        try:
            answered = await plan_requests.answer(
                conn,
                str(request_id),
                decision=body.decision,
                operator_id=op.principal_id,
                note=body.note,
            )
            await notify_plan_answer(conn, answered)
            return answered
        except PlanRequestError as e:
            raise ZidError(
                e.code, e.detail or e.code, http_status=_PLAN_REQUEST_STATUS.get(e.code, 422)
            ) from e


class ServiceRequestMove(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["IN_PROGRESS", "ACTIVE", "DECLINED"]
    note: str | None = Field(default=None, max_length=1000)


@router.get("/v1/commercial/service-requests")
async def open_service_requests(request: Request, op: OperatorDep) -> dict[str, Any]:
    """Every company's open requests for dedicated isolation, sovereign service, key custody or named support, oldest first."""
    async with (
        tenant_transaction(request_engine(request), request.state.operator_ctx) as conn,
        operator_scope(conn),
    ):
        return {"items": await service_requests.open_requests(conn)}


async def notify_service_move(conn: Any, moved: dict[str, Any]) -> None:
    """Tell the company, in its own Alerts, where its request stands. Raised in the company's tenant context, never the operator's."""
    from zid_platform.modules.notification.ports import AlertDraft, Severity, raise_alert

    words = {
        "IN_PROGRESS": "has been started",
        "ACTIVE": "is now in place",
        "DECLINED": "was declined",
    }[moved["status"]]
    service = str(moved["service"]).replace("_", " ").lower()
    draft = AlertDraft(
        severity=Severity.INFO,
        kind="commercial.service_request.moved",
        title=f"Your request for {service} {words}",
        body=moved.get("note") or "",
        object_type="service_request",
        object_ref=moved["request_id"],
        href="/settings",
        recommended_action="Open Settings, Enterprise services, to see the request.",
        is_task=False,
        mandatory=False,
        correlation_key=f"service_request.moved:{moved['request_id']}:{moved['status']}",
    )
    async with in_tenant(conn, moved["tenant_id"]):
        await raise_alert(conn, moved["tenant_id"], draft, now=datetime.now(UTC))


@router.post("/v1/commercial/service-requests/{request_id}:move")
async def move_service_request(
    request_id: uuid.UUID, body: ServiceRequestMove, request: Request, op: OperatorDep
) -> dict[str, Any]:
    async with (
        tenant_transaction(request_engine(request), request.state.operator_ctx) as conn,
        operator_scope(conn),
    ):
        try:
            moved = await service_requests.move(
                conn,
                str(request_id),
                target=body.status,
                operator_id=op.principal_id,
                note=body.note,
            )
            await notify_service_move(conn, moved)
            return moved
        except ServiceRequestError as e:
            raise ZidError(
                e.code,
                e.detail,
                http_status=404
                if e.code.endswith("NOT_FOUND")
                else 409
                if "REFUSED" in e.code
                else 422,
            ) from e


class DeletionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason_ref: str = Field(
        min_length=4, max_length=128
    )  # the case or legal reference that asks for the deletion


@router.post("/v1/commercial/tenants/{tenant_id}/data-deletion", status_code=202)
async def request_data_deletion(
    tenant_id: uuid.UUID, body: DeletionBody, request: Request, op: OperatorDep
) -> dict[str, Any]:
    """Ask for a CLOSED tenant's data to be deleted. The worker erases what no legal hold protects and reports what it
    kept; the tenant must already be closed (offboarding drains it first). TEN-25."""
    deletion = request.app.state.tenant_deletion

    tenant = str(tenant_id)
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _require(conn, op, "tenant.data.delete", tenant, f"tenant:{tenant}")
        async with in_tenant(conn, tenant):
            try:
                await deletion.request(
                    conn,
                    tenant,
                    requested_by=f"{op.principal_id}:{body.reason_ref}",
                    now=datetime.now(UTC),
                )
            except deletion.error as e:
                raise ZidError(
                    e.code, str(e), http_status=404 if e.code == "TENANT_NOT_FOUND" else 409
                ) from e
    return {"tenant_id": tenant, "status": "REQUESTED"}


@router.get("/v1/commercial/tenants/{tenant_id}/data-deletion")
async def data_deletion_status(
    tenant_id: uuid.UUID, request: Request, op: OperatorDep
) -> dict[str, Any]:
    tenant = str(tenant_id)
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _require(conn, op, "tenant.data.delete", tenant, f"tenant:{tenant}")
        async with in_tenant(conn, tenant):
            row = await request.app.state.tenant_deletion.status(conn, tenant)
    if row is None:
        raise ZidError(
            "DELETION_NOT_REQUESTED",
            "No deletion has been requested for this tenant.",
            http_status=404,
        )
    return dict(row)


class EnforcementBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["ENFORCE", "SHADOW"]
    reason: str = Field(min_length=3, max_length=500)


@router.get("/v1/commercial/tenants/{tenant_id}/enforcement")
async def get_enforcement(
    tenant_id: uuid.UUID, request: Request, op: OperatorDep
) -> dict[str, Any]:
    async with (
        tenant_transaction(request_engine(request), request.state.operator_ctx) as conn,
        operator_scope(conn),
    ):
        return await enforcement_store.read(
            conn, str(tenant_id), getattr(request.app.state, "commercial_enforcement", "shadow")
        )


@router.put("/v1/commercial/tenants/{tenant_id}/enforcement")
async def set_enforcement(
    tenant_id: uuid.UUID, body: EnforcementBody, request: Request, op: OperatorDep
) -> dict[str, Any]:
    """ENFORCE: this company's plan limits refuse for real, ahead of the deployment default. SHADOW: they only report. A deployment that
    enforces for everyone still enforces for this company either way; the setting can only make one company stricter, never looser."""
    tenant = str(tenant_id)
    async with (
        tenant_transaction(request_engine(request), request.state.operator_ctx) as conn,
        operator_scope(conn),
    ):
        await _require(conn, op, "tenant.config.change", tenant, f"tenant:{tenant}")
        await enforcement_store.write(
            conn, tenant, mode=body.mode, reason=body.reason.strip(), actor=op.principal_id
        )
        await log_change(
            conn,
            kind=f"ENFORCEMENT_{body.mode}",
            ref=tenant,
            tenant_id=tenant,
            actor=op.principal_id,
            reason=body.reason.strip(),
            detail={},
        )
        return await enforcement_store.read(
            conn, tenant, getattr(request.app.state, "commercial_enforcement", "shadow")
        )


@router.put("/v1/commercial/tenants/{tenant_id}/entitlement")
async def set_plan(
    tenant_id: uuid.UUID, body: PlanBody, request: Request, op: OperatorDep
) -> dict[str, Any]:
    tenant = str(tenant_id)
    now = datetime.now(UTC)
    if (
        body.plan not in PLAN_CODES
    ):  # the Literal already refuses; kept as the single source of truth
        raise ZidError("VALIDATION_FAILED", "Unknown plan.", http_status=422)
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _require(conn, op, "tenant.config.change", tenant, f"tenant:{tenant}")
        book = await store.approved_price_book(conn, now)
        async with in_tenant(conn, tenant):
            try:
                result = await store.set_entitlement(
                    conn,
                    tenant_id=tenant,
                    plan_code=body.plan,
                    price_book_id=str(book["price_book_id"]) if book else None,
                    actor=op.principal_id,
                    now=now,
                )
            except CommercialError as e:
                raise _fail(e) from e
            await entitlement_changed(
                conn,
                tenant_id=tenant,
                entitlement_id=result["entitlement_id"],
                version=int(result["version"]),
                plan=body.plan,
                previous_plan=result["previous_plan"],
                price_book_version=book["version"] if book else None,
                effective_from=now,
            )
            await log_change(
                conn,
                kind="ENTITLEMENT_CHANGED",
                ref=result["entitlement_id"],
                tenant_id=tenant,
                actor=op.principal_id,
                reason="OPERATOR_PLAN_CHANGE",
                detail={"plan": body.plan, "previous_plan": result["previous_plan"]},
            )
    return {**result, "effective_from": now.isoformat()}


@router.post("/v1/commercial/tenants/{tenant_id}/overrides", status_code=201)
async def add_override(
    tenant_id: uuid.UUID, body: OverrideBody, request: Request, op: OperatorDep
) -> dict[str, Any]:
    tenant = str(tenant_id)
    if body.grant_mode is None and body.extra_count is None:
        raise ZidError(
            "VALIDATION_FAILED", "A grant mode or an extra count is required.", http_status=422
        )
    if direct_mode():
        approver_id = DIRECT_APPROVER
    else:
        if body.approver_id is None:
            raise ZidError("VALIDATION_FAILED", "An approver is required.", http_status=422)
        approver_id = str(body.approver_id)
        if approver_id == op.principal_id:
            raise ZidError(
                "SELF_APPROVAL", "An override needs a different approver.", http_status=403
            )
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _require(conn, op, "tenant.config.change", tenant, f"tenant:{tenant}")
        if not direct_mode() and not await is_active_operator(conn, op.tenant_id, approver_id):
            raise ZidError(
                "APPROVER_INVALID", "The approver must be an active operator.", http_status=403
            )
        async with in_tenant(conn, tenant):
            oid = await store.add_override(
                conn,
                tenant_id=tenant,
                capability_key=body.capability,
                grant_mode=body.grant_mode,
                extra_count=body.extra_count,
                reason=body.reason,
                approved_by=approver_id,
                created_by=op.principal_id,
                expires_at=body.expires_at,
            )
            await log_change(
                conn,
                kind="ENTITLEMENT_OVERRIDE",
                ref=oid,
                tenant_id=tenant,
                actor=op.principal_id,
                reason=body.reason,
                detail={
                    "capability": body.capability,
                    "extra_count": body.extra_count,
                    "approver": approver_id,
                },
            )
    return {"override_id": oid, "capability": body.capability}


# ------------------------------------------------------------------------------- billing operations


class AdjustmentBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_id: uuid.UUID
    quantity_delta: Decimal = Field(max_digits=18, decimal_places=6)
    reason_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{2,40}$")

    @field_validator("quantity_delta")
    @classmethod
    def _non_zero(cls, v: Decimal) -> Decimal:
        if v == 0:
            raise ValueError("an adjustment changes the quantity")
        return v


def _month(value: str) -> date:
    try:
        y, m = value.split("-")
        return date(int(y), int(m), 1)
    except ValueError as e:
        raise ZidError("VALIDATION_FAILED", "month must be YYYY-MM.", http_status=422) from e


@router.post("/v1/commercial/tenants/{tenant_id}/meter-windows/{month}:freeze")
async def freeze_window(
    tenant_id: uuid.UUID, month: str, request: Request, op: OperatorDep
) -> dict[str, Any]:
    """Close every meter of one tenant for a finished UTC month; the database then refuses new events there."""
    tenant, m = str(tenant_id), _month(month)
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _require(conn, op, "commercial.billing.manage", tenant, f"tenant:{tenant}")
        async with in_tenant(conn, tenant):
            try:
                frozen = await freeze_month(conn, tenant, m)
            except BillableError as e:
                raise ZidError(e.code, e.detail or e.code, http_status=409) from e
            if frozen:
                await meter_window_frozen(
                    conn, tenant_id=tenant, month=m.isoformat(), meters=len(frozen)
                )
            summary = await ledger_summary(conn, m)
    return {"frozen_now": [f["meter"] for f in frozen], "summary": summary}


@router.post("/v1/commercial/tenants/{tenant_id}/adjustments", status_code=201)
async def request_adjustment(
    tenant_id: uuid.UUID, body: AdjustmentBody, request: Request, op: OperatorDep
) -> dict[str, Any]:
    """Ask to correct a billable event. The original is never edited: an approved adjustment is a signed
    delta in the window that is still open."""
    tenant = str(tenant_id)
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _require(conn, op, "commercial.billing.manage", tenant, f"tenant:{tenant}")
        async with in_tenant(conn, tenant):
            event = await billable_store.load_event(conn, str(body.event_id))
            if event is None:
                raise ZidError(
                    "BILLABLE_EVENT_NOT_FOUND", "No such billable event.", http_status=404
                )
            month = event["window_month"]
            if event["meter_id"] in await billable_store.frozen_windows(conn, month):
                month = next_month(
                    month
                )  # a closed window is never reopened: the correction lands in the next
            aid = await billable_store.request_adjustment(
                conn,
                tenant_id=tenant,
                event_id=str(body.event_id),
                meter_id=event["meter_id"],
                window_month=month,
                quantity_delta=body.quantity_delta,
                reason_code=body.reason_code,
                requested_by=op.principal_id,
            )
    return {"adjustment_id": aid, "state": "REQUESTED", "window_month": month.isoformat()}


@router.post("/v1/commercial/tenants/{tenant_id}/adjustments/{adjustment_id}:decide")
async def decide_adjustment(
    tenant_id: uuid.UUID,
    adjustment_id: uuid.UUID,
    approve: bool,
    request: Request,
    op: OperatorDep,
) -> dict[str, Any]:
    tenant = str(tenant_id)
    now = datetime.now(UTC)
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _require(conn, op, "commercial.billing.manage", tenant, f"tenant:{tenant}")
        async with in_tenant(conn, tenant):
            adj = await billable_store.load_adjustment(conn, str(adjustment_id))
            if adj is None:
                raise ZidError("ADJUSTMENT_NOT_FOUND", "No such adjustment.", http_status=404)
            if adj["state"] != "REQUESTED":
                raise ZidError(
                    "ADJUSTMENT_FINAL", "A decided adjustment is final.", http_status=409
                )
            if not direct_mode() and str(adj["requested_by"]) == op.principal_id:
                raise ZidError(
                    "SELF_APPROVAL", "An adjustment needs a different approver.", http_status=403
                )
            await billable_store.decide_adjustment(
                conn, str(adjustment_id), approver=_approver(op), approve=approve, now=now
            )
            await billable_event_adjusted(
                conn,
                tenant_id=tenant,
                adjustment_id=str(adjustment_id),
                meter=adj["meter_id"],
                month=adj["window_month"].isoformat(),
                state="APPROVED" if approve else "REJECTED",
            )
            await log_change(
                conn,
                kind="USAGE_ADJUSTMENT_DECIDED",
                ref=str(adjustment_id),
                tenant_id=tenant,
                actor=op.principal_id,
                reason=adj["reason_code"],
                detail={
                    "state": "APPROVED" if approve else "REJECTED",
                    "delta": str(adj["quantity_delta"]),
                },
            )
    return {"adjustment_id": str(adjustment_id), "state": "APPROVED" if approve else "REJECTED"}


@router.post("/v1/commercial/tenants/{tenant_id}/reconcile/{month}")
async def reconcile_ledger(
    tenant_id: uuid.UUID, month: str, request: Request, op: OperatorDep
) -> dict[str, Any]:
    """Compare the ledger with the authoritative rows (sessions, credentials) for one month and record what
    is missing, marked `recovered`. Nothing is invented without a source row and nothing existing is touched.
    A frozen window refuses new events, so the recovered ones land in the next open window, flagged late."""
    tenant, m = str(tenant_id), _month(month)
    start = datetime(m.year, m.month, 1, tzinfo=UTC)
    end = datetime.combine(next_month(m), datetime.min.time(), tzinfo=UTC)
    sources = getattr(request.app.state, "commercial_sources", {})
    recovered: dict[str, int] = {}
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _require(conn, op, "commercial.billing.manage", tenant, f"tenant:{tenant}")
        async with in_tenant(conn, tenant):
            for meter, (fetch, make_key, pclass) in sources.items():
                rows = await fetch(conn, start, end)
                recovered[meter] = await reconcile(
                    conn,
                    tenant_id=tenant,
                    meter=meter,
                    sources=[(make_key(rid), at, pclass) for rid, at in rows],
                )
    return {"month": m.isoformat(), "recovered": recovered}


# ------------------------------------------------------------------------------- rating


class CreditBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    amount: Decimal = Field(gt=0, max_digits=18, decimal_places=6)
    reason_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{2,40}$")
    reverses: str | None = Field(default=None, max_length=64)


class RatingBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    month: str = Field(pattern=r"^\d{4}-\d{2}$")
    credits: list[CreditBody] = Field(default_factory=list, max_length=20)


@router.post("/v1/commercial/tenants/{tenant_id}/rating-runs")
async def create_rating_run(
    tenant_id: uuid.UUID, body: RatingBody, request: Request, op: OperatorDep
) -> dict[str, Any]:
    """Rate one tenant's frozen month. The same inputs return the same run (the replay proof); new inputs
    supersede the previous run and keep its lines. Connections are counted now, at the cut-off."""
    tenant, m = str(tenant_id), _month(body.month)
    counters = getattr(request.app.state, "commercial_counters", {})
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _require(conn, op, "commercial.billing.manage", tenant, f"tenant:{tenant}")
        async with in_tenant(conn, tenant):
            connections = {key: await count(conn) for key, count in counters.items()}
            try:
                return await run_rating(
                    conn,
                    tenant_id=tenant,
                    month=m,
                    connections=connections,
                    credits=[Credit(c.amount, c.reason_code, c.reverses) for c in body.credits],
                    actor=op.principal_id,
                )
            except RatingError as e:
                status = (
                    409
                    if e.code in ("WINDOW_NOT_FROZEN", "NO_PRICE_BOOK", "CURRENCY_MIXED")
                    else 500
                )
                raise ZidError(e.code, e.detail or e.code, http_status=status) from e


# ------------------------------------------------------------------------------- invoicing


class DraftInvoiceBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    month: str = Field(pattern=r"^\d{4}-\d{2}$")


class IssueBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    payment_terms_days: int = Field(ge=0, le=365)
    allow_zero_tax: bool = False


class CreditNoteBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    amount: Decimal = Field(gt=0, max_digits=18, decimal_places=2)
    reason_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{2,40}$")
    reverses_line: int | None = Field(default=None, ge=1)


class CollectBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    idempotency_key: str = Field(min_length=8, max_length=128)


class StateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    to: str = Field(pattern=r"^[A-Z_]{4,20}$")
    reason_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{2,40}$")


class SafetyHoldBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    on: bool
    reason: str | None = Field(default=None, min_length=3, max_length=500)


def _inv_fail(e: InvoicingError) -> ZidError:
    status = {
        "INVOICE_NOT_FOUND": 404,
        "RATING_NOT_FOUND": 409,
        "INVOICE_SELF_ISSUE": 403,
        "TAX_NOT_CONFIGURED": 409,
        "BILLING_TRANSITION_INVALID": 409,
        "BILLING_REASON_INVALID": 422,
        "BILLING_STATE_UNKNOWN": 422,
        "CREDIT_NOTE_EXCEEDS_INVOICE": 409,
        "SAFETY_REASON_REQUIRED": 422,
    }.get(e.code, 409)
    return ZidError(e.code, e.detail or e.code, http_status=status)


@router.post("/v1/commercial/tenants/{tenant_id}/invoices", status_code=201)
async def draft_invoice(
    tenant_id: uuid.UUID, body: DraftInvoiceBody, request: Request, op: OperatorDep
) -> dict[str, Any]:
    tenant = str(tenant_id)
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _require(conn, op, "commercial.billing.manage", tenant, f"tenant:{tenant}")
        async with in_tenant(conn, tenant):
            try:
                return await invoicing.create_draft(
                    conn,
                    tenant_id=tenant,
                    month=_month(body.month),
                    issuing_entity=request.app.state.commercial_issuing_entity,
                    actor=op.principal_id,
                )
            except InvoicingError as e:
                raise _inv_fail(e) from e


@router.post("/v1/commercial/tenants/{tenant_id}/invoices/{invoice_id}:issue")
async def issue_invoice(
    tenant_id: uuid.UUID, invoice_id: uuid.UUID, body: IssueBody, request: Request, op: OperatorDep
) -> dict[str, Any]:
    tenant = str(tenant_id)
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _require(conn, op, "commercial.billing.manage", tenant, f"tenant:{tenant}")
        async with in_tenant(conn, tenant):
            try:
                return await invoicing.issue(
                    conn,
                    str(invoice_id),
                    issuer=_approver(op),
                    tax=request.app.state.tax_port,
                    payment_terms_days=body.payment_terms_days,
                    allow_zero_tax=body.allow_zero_tax,
                    region=request.app.state.commercial_region,
                )
            except InvoicingError as e:
                raise _inv_fail(e) from e


@router.post("/v1/commercial/tenants/{tenant_id}/invoices/{invoice_id}:void")
async def void_invoice(
    tenant_id: uuid.UUID, invoice_id: uuid.UUID, request: Request, op: OperatorDep
) -> dict[str, Any]:
    tenant = str(tenant_id)
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _require(conn, op, "commercial.billing.manage", tenant, f"tenant:{tenant}")
        async with in_tenant(conn, tenant):
            try:
                await invoicing.void(conn, str(invoice_id))
            except InvoicingError as e:
                raise _inv_fail(e) from e
    return {"invoice_id": str(invoice_id), "status": "VOID"}


@router.post(
    "/v1/commercial/tenants/{tenant_id}/invoices/{invoice_id}/credit-notes", status_code=201
)
async def issue_credit_note(
    tenant_id: uuid.UUID,
    invoice_id: uuid.UUID,
    body: CreditNoteBody,
    request: Request,
    op: OperatorDep,
) -> dict[str, Any]:
    tenant = str(tenant_id)
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _require(conn, op, "commercial.billing.manage", tenant, f"tenant:{tenant}")
        async with in_tenant(conn, tenant):
            try:
                return await invoicing.credit_note(
                    conn,
                    str(invoice_id),
                    amount=body.amount,
                    reason_code=body.reason_code,
                    reverses_line=body.reverses_line,
                    issuer=op.principal_id,
                )
            except InvoicingError as e:
                raise _inv_fail(e) from e


@router.post("/v1/commercial/tenants/{tenant_id}/invoices/{invoice_id}:collect")
async def collect_invoice(
    tenant_id: uuid.UUID,
    invoice_id: uuid.UUID,
    body: CollectBody,
    request: Request,
    op: OperatorDep,
) -> dict[str, Any]:
    tenant = str(tenant_id)
    payments = getattr(request.app.state, "payment_port", None)
    if payments is None:
        raise ZidError(
            "PAYMENT_NOT_CONFIGURED",
            "No payment processor is configured in this cell.",
            http_status=503,
        )
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _require(conn, op, "commercial.billing.manage", tenant, f"tenant:{tenant}")
        async with in_tenant(conn, tenant):
            try:
                return await invoicing.collect(
                    conn, str(invoice_id), payments=payments, idempotency_key=body.idempotency_key
                )
            except InvoicingError as e:
                raise _inv_fail(e) from e


@router.post("/v1/commercial/tenants/{tenant_id}/billing-state")
async def move_billing_state(
    tenant_id: uuid.UUID, body: StateBody, request: Request, op: OperatorDep
) -> dict[str, Any]:
    tenant = str(tenant_id)
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _require(conn, op, "commercial.billing.manage", tenant, f"tenant:{tenant}")
        async with in_tenant(conn, tenant):
            try:
                return await invoicing.transition(
                    conn, tenant, body.to, body.reason_code, actor=op.principal_id
                )
            except InvoicingError as e:
                raise _inv_fail(e) from e


@router.post("/v1/commercial/tenants/{tenant_id}/safety-hold")
async def safety_hold(
    tenant_id: uuid.UUID, body: SafetyHoldBody, request: Request, op: OperatorDep
) -> dict[str, Any]:
    """An abuse or acceptable-use hold: separate from billing, never cleared by paying (CR-027)."""
    tenant = str(tenant_id)
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _require(conn, op, "commercial.billing.manage", tenant, f"tenant:{tenant}")
        async with in_tenant(conn, tenant):
            try:
                await invoicing.set_safety_hold(conn, tenant, on=body.on, reason=body.reason)
            except InvoicingError as e:
                raise _inv_fail(e) from e
            return await invoicing.billing_state(conn)


@router.post("/v1/commercial/tenants/{tenant_id}/dunning:run")
async def dunning(tenant_id: uuid.UUID, request: Request, op: OperatorDep) -> dict[str, Any]:
    tenant = str(tenant_id)
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _require(conn, op, "commercial.billing.manage", tenant, f"tenant:{tenant}")
        async with in_tenant(conn, tenant):
            moves = await invoicing.run_dunning(conn, tenant)
            return {"moves": moves, "state": await invoicing.billing_state(conn)}


@router.get("/v1/commercial/invoice-chain:verify")
async def verify_invoice_chain(request: Request, op: OperatorDep) -> dict[str, Any]:
    """Walk this cell's issuing entity's chain: digests only, no tenant data. Reports the first broken link."""
    entity = request.app.state.commercial_issuing_entity
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        entries = await invoicing.chain_entries(conn, entity)
    broken = verify_chain(entries)
    return {
        "issuing_entity": entity,
        "entries": len(entries),
        "intact": broken is None,
        "first_broken": broken,
    }


# ------------------------------------------------------------------------------- accounts, quotes, orders


class AccountBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    legal_name: str = Field(min_length=1, max_length=300)
    kind: Literal["DIRECT", "PARTNER", "INTERNAL"] = "DIRECT"
    country: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")
    currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")
    reseller_of: uuid.UUID | None = None


class RegionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sre_gate_passed: bool
    evidence_ref: str = Field(min_length=3, max_length=300)


class RoleBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    principal_id: uuid.UUID
    role: Literal["AE", "CCO", "CFO", "FOUNDER", "GROUP_CFO"]


class QuoteLineBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sku: str = Field(pattern=r"^ZID-[A-Z0-9]{2,8}$")
    plan: PlanCode
    quantity: Decimal = Field(gt=0, max_digits=18, decimal_places=6)
    discount_pct: Decimal = Field(default=Decimal(0), ge=0, lt=1, max_digits=6, decimal_places=4)
    promotions: list[Annotated[Decimal, Field(ge=0, lt=1, max_digits=6, decimal_places=4)]] = Field(
        default_factory=list, max_length=5
    )


class QuoteBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account_id: uuid.UUID
    price_book_id: uuid.UUID
    region: str = Field(pattern=r"^[a-z0-9-]{1,40}$")
    term_months: int = Field(ge=1, le=60)
    lines: list[QuoteLineBody] = Field(min_length=1, max_length=50)
    prepaid: bool = False
    prepaid_discount: Decimal = Field(
        default=Decimal(0), ge=0, lt=1, max_digits=6, decimal_places=4
    )
    liability_capped: bool = True
    residency_commitment: str | None = Field(default=None, pattern=r"^[a-z0-9-]{1,40}$")
    pilot: bool = False


class ApproveQuoteBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["AE", "CCO", "CFO", "FOUNDER", "GROUP_CFO"]


class AcceptBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    quote_id: uuid.UUID
    signer_ref: str = Field(min_length=3, max_length=200)
    signed_document_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    effective_from: datetime
    amends_order_id: uuid.UUID | None = None


def _order_fail(e: OrderError) -> ZidError:
    status = {
        "QUOTE_NOT_FOUND": 404,
        "ORDER_NOT_FOUND": 404,
        "ACCOUNT_NOT_FOUND": 404,
        "PRICE_BOOK_NOT_FOUND": 404,
        "QUOTE_SELF_APPROVAL": 403,
        "APPROVER_ROLE_MISSING": 403,
        "ORDER_SELF_ACTIVATION": 403,
        "POLICY_NOT_SIGNED": 403,
    }.get(e.code, 409)
    return ZidError(e.code, e.detail or e.code, http_status=status)


async def _sales(conn: Any, op: Any) -> None:
    await _require(conn, op, "commercial.sales.manage", None, "commercial:sales")


@router.post("/v1/commercial/accounts", status_code=201)
async def create_account(body: AccountBody, request: Request, op: OperatorDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _sales(conn, op)
        aid = await orders.create_account(
            conn,
            legal_name=body.legal_name,
            kind=body.kind,
            country=body.country,
            currency=body.currency,
            reseller_of=str(body.reseller_of) if body.reseller_of else None,
            actor=op.principal_id,
        )
    return {"account_id": aid}


class PaymentCustomerBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: Literal["stripe"] = "stripe"
    customer_ref: str = Field(pattern=r"^cus_[A-Za-z0-9]{6,64}$")


@router.put("/v1/commercial/accounts/{account_id}/payment-customer")
async def set_payment_customer(
    account_id: uuid.UUID, body: PaymentCustomerBody, request: Request, op: OperatorDep
) -> dict[str, Any]:
    """Link an account to its customer id at the payment processor: a reference, never card data."""
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _sales(conn, op)
        try:
            await invoicing.set_payment_customer(
                conn,
                str(account_id),
                provider=body.provider,
                customer_ref=body.customer_ref,
                actor=op.principal_id,
            )
        except IntegrityError as e:
            raise ZidError("ACCOUNT_NOT_FOUND", "No such account.", http_status=404) from e
        await log_change(
            conn,
            kind="PAYMENT_CUSTOMER_SET",
            ref=str(account_id),
            tenant_id=None,
            actor=op.principal_id,
            detail={"provider": body.provider},
        )
    return {"account_id": str(account_id), "provider": body.provider}


@router.post("/v1/commercial/accounts/{account_id}/tenants/{tenant_id}")
async def link_account_tenant(
    account_id: uuid.UUID, tenant_id: uuid.UUID, request: Request, op: OperatorDep
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _sales(conn, op)
        await orders.link_tenant(conn, str(account_id), str(tenant_id))
    return {"account_id": str(account_id), "tenant_id": str(tenant_id)}


@router.put("/v1/commercial/regions/{region}")
async def record_region_release(
    region: str, body: RegionBody, request: Request, op: OperatorDep
) -> dict[str, Any]:
    """Record whether a cell's SRE release gate has passed; only released regions can be quoted."""
    if not re.fullmatch(r"[a-z0-9-]{1,40}", region):
        raise ZidError("VALIDATION_FAILED", "Bad region.", http_status=422)
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _require(conn, op, "commercial.catalog.manage", None, "commercial:catalogue")
        await conn.execute(
            text(
                "INSERT INTO commercial.region_release (region, sre_gate_passed, evidence_ref, recorded_by) "
                "VALUES (:r, :g, :e, :b) ON CONFLICT (region) DO UPDATE SET sre_gate_passed = EXCLUDED.sre_gate_passed, "
                "evidence_ref = EXCLUDED.evidence_ref, recorded_by = EXCLUDED.recorded_by, recorded_at = now()"
            ),
            {
                "r": region,
                "g": body.sre_gate_passed,
                "e": body.evidence_ref,
                "b": uuid.UUID(op.principal_id),
            },
        )
    return {"region": region, "sre_gate_passed": body.sre_gate_passed}


@router.post("/v1/commercial/role-holders", status_code=201)
async def grant_commercial_role(
    body: RoleBody, request: Request, op: OperatorDep
) -> dict[str, Any]:
    """Who may approve discounts in which band. Never granted to oneself."""
    if str(body.principal_id) == op.principal_id:
        raise ZidError(
            "SELF_GRANT", "A commercial role is granted by someone else.", http_status=403
        )
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _require(conn, op, "commercial.catalog.manage", None, "commercial:catalogue")
        try:
            await finance.require_person(
                conn, str(body.principal_id), body.role
            )  # a discount approver is a person, never a service account
        except FinanceError as e:
            raise ZidError(e.code, e.detail, http_status=422) from e
        await conn.execute(
            text(
                "INSERT INTO commercial.commercial_role_holder (principal_id, role, granted_by) VALUES (:p, :r, :g) "
                "ON CONFLICT DO NOTHING"
            ),
            {"p": body.principal_id, "r": body.role, "g": uuid.UUID(op.principal_id)},
        )
    return {"principal_id": str(body.principal_id), "role": body.role}


@router.post("/v1/commercial/quotes", status_code=201)
async def create_quote(body: QuoteBody, request: Request, op: OperatorDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _sales(conn, op)
        try:
            return await orders.create_quote(
                conn,
                account_id=str(body.account_id),
                price_book_id=str(body.price_book_id),
                region=body.region,
                term_months=body.term_months,
                lines=[
                    QuoteLine(
                        i, ln.sku, ln.plan, ln.quantity, ln.discount_pct, tuple(ln.promotions)
                    )
                    for i, ln in enumerate(body.lines, start=1)
                ],
                prepaid=body.prepaid,
                prepaid_discount=body.prepaid_discount,
                liability_capped=body.liability_capped,
                residency_commitment=body.residency_commitment,
                pilot=body.pilot,
                actor=op.principal_id,
            )
        except OrderError as e:
            raise _order_fail(e) from e


@router.get("/v1/commercial/quotes/{quote_id}")
async def get_quote(quote_id: uuid.UUID, request: Request, op: OperatorDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _sales(conn, op)
        try:
            return await orders.quote_view(conn, str(quote_id))
        except OrderError as e:
            raise _order_fail(e) from e


@router.post("/v1/commercial/quotes/{quote_id}:approve")
async def approve_quote(
    quote_id: uuid.UUID, body: ApproveQuoteBody, request: Request, op: OperatorDep
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _sales(conn, op)
        try:
            return await orders.approve_quote(
                conn,
                str(quote_id),
                role=body.role,
                approver=_approver(op),
                direct=direct_mode(),
                policy_lock=str(getattr(request.app.state, "environment", "production"))
                not in ("local", "test"),
            )
        except OrderError as e:
            raise _order_fail(e) from e


@router.post("/v1/commercial/tenants/{tenant_id}/orders", status_code=201)
async def accept_quote(
    tenant_id: uuid.UUID, body: AcceptBody, request: Request, op: OperatorDep
) -> dict[str, Any]:
    """Record the buyer's signature on a quote: it becomes an order for this tenant of the quoted account."""
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _sales(conn, op)
        try:
            return await orders.accept(
                conn,
                str(body.quote_id),
                tenant_id=str(tenant_id),
                signer_ref=body.signer_ref,
                signed_document_digest=body.signed_document_digest,
                effective_from=body.effective_from,
                amends_order_id=str(body.amends_order_id) if body.amends_order_id else None,
                actor=op.principal_id,
            )
        except OrderError as e:
            raise _order_fail(e) from e


@router.post("/v1/commercial/orders/{order_id}:activate")
async def activate_order(order_id: uuid.UUID, request: Request, op: OperatorDep) -> dict[str, Any]:
    """The only route from a signed order to a tenant's entitlement. Idempotent; a second person activates."""
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _sales(conn, op)
        require = getattr(request.app.state, "activation_payment", "REQUIRED") == "REQUIRED"
        try:
            done = await orders.activate(
                conn,
                str(order_id),
                actor=op.principal_id,
                direct=direct_mode(),
                require_payment=require,
            )
        except OrderError as e:
            raise _order_fail(e) from e
        if (
            done.get("awaiting_payment")
            and getattr(request.app.state, "payment_setup", None) is None
        ):
            # fail closed: a plan that must be paid for is not switched on, and nobody could pay for it. Raising rolls the hold back.
            raise ZidError(
                "ACTIVATION_PAYMENT_UNAVAILABLE",
                "This order has a monthly fee to pay first, but card payments are not set up on this platform. Set them up, or set ZID_ACTIVATION_PAYMENT=OFF.",
                http_status=409,
            )
        return done


@router.post("/v1/commercial/orders/{order_id}:cancel-payment-wait")
async def cancel_payment_wait(
    order_id: uuid.UUID, request: Request, op: OperatorDep
) -> dict[str, Any]:
    """Take an order back from "awaiting payment" to signed. Nothing was charged; a paid one cannot be taken back here."""
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _sales(conn, op)
        try:
            o = await orders.order_view(conn, str(order_id))
            return await activations_service.cancel(
                conn, o["tenant_id"], str(order_id), actor=op.principal_id
            )
        except OrderError as e:
            raise _order_fail(e) from e
        except activations_service.ActivationError as e:
            raise ZidError(e.code, e.detail, http_status=409) from e


class ActivationRefundBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=3, max_length=500)


@router.post("/v1/commercial/orders/{order_id}:refund-activation")
async def refund_activation(
    order_id: uuid.UUID, body: ActivationRefundBody, request: Request, op: OperatorDep
) -> dict[str, Any]:
    """Mark a paid, unused activation payment as to be returned. The refund is made at the processor; its report marks the payment refunded."""
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _sales(conn, op)
        try:
            o = await orders.order_view(conn, str(order_id))
            return await activations_service.request_refund(
                conn, o["tenant_id"], str(order_id), actor=op.principal_id, reason=body.reason
            )
        except OrderError as e:
            raise _order_fail(e) from e
        except activations_service.ActivationError as e:
            raise ZidError(e.code, e.detail, http_status=409) from e


@router.get("/v1/commercial/activation-refunds")
async def activation_refunds(request: Request, op: OperatorDep) -> dict[str, Any]:
    """Plan-activation payments waiting to be returned, with the processor reference to refund."""
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _sales(conn, op)
        return {"items": await activations_service.refunds_due(conn)}


# ------------------------------------------------------------------------------- vendor costs


class VendorBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    vendor_id: str = Field(pattern=r"^[a-z0-9-]{2,40}$")
    display_name: str = Field(min_length=1, max_length=200)
    legal_role: Literal["SUBPROCESSOR", "INDEPENDENT_CONTROLLER"]
    adapter_id: str | None = Field(default=None, max_length=200)
    sku: Literal["ZID-IDV", "ZID-KYB", "ZID-RISK", "ZID-MSG"]
    spike_threshold_pct: Decimal | None = Field(default=None, gt=0, max_digits=6, decimal_places=2)


class RecognitionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    basis: Literal["PRINCIPAL", "AGENT", "UNDECIDED"]


class VendorRateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    check_type: str = Field(pattern=r"^[A-Z][A-Z0-9_]{1,40}$")
    country: str = Field(pattern=r"^[A-Z]{2}$")
    base_price: Decimal = Field(ge=0, max_digits=18, decimal_places=6)
    retry_price: Decimal = Field(ge=0, max_digits=18, decimal_places=6)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    confidence: Literal["SIGNED_TARIFF", "QUOTED", "ESTIMATE"]
    source_ref: str = Field(min_length=3, max_length=300)


class RouteBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["ACTIVE", "BLOCKED", "NEEDS_APPROVAL"]
    reason: str = Field(min_length=3, max_length=200)


class VendorInvoiceLineBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    supplier_ref: str = Field(min_length=1, max_length=200)
    amount: Decimal = Field(max_digits=18, decimal_places=6)
    currency: str = Field(pattern=r"^[A-Z]{3}$")


class VendorInvoiceBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lines: list[VendorInvoiceLineBody] = Field(min_length=1, max_length=10_000)


class ReconcileBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    period_start: datetime
    period_end: datetime


def _vendor_fail(e: VendorError) -> ZidError:
    return ZidError(
        e.code, e.detail or e.code, http_status=404 if e.code == "VENDOR_NOT_FOUND" else 422
    )


async def _catalog_grant(conn: Any, op: Any) -> None:
    await _require(conn, op, "commercial.catalog.manage", None, "commercial:catalogue")


@router.post("/v1/commercial/vendors", status_code=201)
async def register_vendor(body: VendorBody, request: Request, op: OperatorDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _catalog_grant(conn, op)
        await vendors.register(
            conn,
            vendor_id=body.vendor_id,
            display_name=body.display_name,
            legal_role=body.legal_role,
            adapter_id=body.adapter_id,
            sku_id=body.sku,
            spike_threshold_pct=body.spike_threshold_pct,
            actor=op.principal_id,
        )
    return {"vendor_id": body.vendor_id}


@router.put("/v1/commercial/vendors/{vendor_id}/recognition")
async def vendor_recognition(
    vendor_id: str, body: RecognitionBody, request: Request, op: OperatorDep
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _catalog_grant(conn, op)
        try:
            await vendors.set_recognition(conn, vendor_id, body.basis)
        except VendorError as e:
            raise _vendor_fail(e) from e
    return {"vendor_id": vendor_id, "recognition_basis": body.basis}


@router.post("/v1/commercial/vendors/{vendor_id}/rates", status_code=201)
async def vendor_rate(
    vendor_id: str, body: VendorRateBody, request: Request, op: OperatorDep
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _catalog_grant(conn, op)
        try:
            return await vendors.record_rate(
                conn,
                vendor_id=vendor_id,
                check_type=body.check_type,
                country=body.country,
                base_price=body.base_price,
                retry_price=body.retry_price,
                currency=body.currency,
                confidence=body.confidence,
                source_ref=body.source_ref,
                actor=op.principal_id,
            )
        except VendorError as e:
            raise _vendor_fail(e) from e


@router.put("/v1/commercial/vendors/{vendor_id}/routes/{check_type}/{country}")
async def vendor_route(
    vendor_id: str,
    check_type: str,
    country: str,
    body: RouteBody,
    request: Request,
    op: OperatorDep,
) -> dict[str, Any]:
    if not re.fullmatch(r"[A-Z][A-Z0-9_]{1,40}", check_type) or not re.fullmatch(
        r"[A-Z]{2}", country
    ):
        raise ZidError("VALIDATION_FAILED", "Bad check type or country.", http_status=422)
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _catalog_grant(conn, op)
        await vendors.set_route(
            conn,
            vendor_id,
            check_type,
            country,
            status=body.status,
            reason=body.reason,
            actor=op.principal_id,
        )
    return {
        "vendor_id": vendor_id,
        "check_type": check_type,
        "country": country,
        "status": body.status,
    }


@router.post("/v1/commercial/vendors/{vendor_id}/invoices/{invoice_ref}", status_code=201)
async def import_vendor_invoice(
    vendor_id: str, invoice_ref: str, body: VendorInvoiceBody, request: Request, op: OperatorDep
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _catalog_grant(conn, op)
        n = await vendors.import_invoice(
            conn,
            vendor_id=vendor_id,
            invoice_ref=invoice_ref[:200],
            lines=[(ln.supplier_ref, ln.amount, ln.currency) for ln in body.lines],
            actor=op.principal_id,
        )
    return {"vendor_id": vendor_id, "invoice_ref": invoice_ref, "lines": n}


@router.post("/v1/commercial/vendors/{vendor_id}/invoices/{invoice_ref}:reconcile")
async def reconcile_vendor_invoice(
    vendor_id: str, invoice_ref: str, body: ReconcileBody, request: Request, op: OperatorDep
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _catalog_grant(conn, op)
        return await vendors.reconcile_invoice(
            conn,
            vendor_id=vendor_id,
            invoice_ref=invoice_ref[:200],
            period_start=body.period_start,
            period_end=body.period_end,
        )


@router.get("/v1/commercial/vendor-costs")
async def vendor_costs(
    request: Request,
    op: OperatorDep,
    start: Annotated[datetime, Query()],
    end: Annotated[datetime, Query()],
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _catalog_grant(conn, op)
        return {"items": await vendors.cost_report(conn, start, end)}


# ------------------------------------------------------------------------------- revenue assurance


class ResolveBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    resolution: str = Field(min_length=3, max_length=500)


class PeriodQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    month: str = Field(pattern=r"^\d{4}-\d{2}$")


@router.get("/v1/commercial/change-log:verify")
async def verify_commercial_change_log(request: Request, op: OperatorDep) -> dict[str, Any]:
    """Recompute every entry's digest and the hash chain; report the first broken entry."""
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _catalog_grant(conn, op)
        return await verify_change_log(conn)


@router.post("/v1/commercial/tenants/{tenant_id}/assurance:run")
async def run_tenant_assurance(
    tenant_id: uuid.UUID, body: PeriodQuery, request: Request, op: OperatorDep
) -> dict[str, Any]:
    tenant = str(tenant_id)
    sources = getattr(request.app.state, "commercial_sources", {})
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _require(conn, op, "commercial.billing.manage", tenant, f"tenant:{tenant}")
        async with in_tenant(conn, tenant):
            found = await assurance.detect_tenant(
                conn, tenant, sources=sources, month=_month(body.month)
            )
    return {"raised": found}


@router.post("/v1/commercial/assurance:run")
async def run_platform_assurance(request: Request, op: OperatorDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _catalog_grant(conn, op)
        return {"raised": await assurance.detect_platform(conn)}


@router.get("/v1/commercial/exceptions")
async def list_exceptions(request: Request, op: OperatorDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _catalog_grant(conn, op)
        return {"items": await assurance.exceptions(conn)}


@router.post("/v1/commercial/exceptions/{exception_id}:resolve")
async def resolve_exception(
    exception_id: uuid.UUID, body: ResolveBody, request: Request, op: OperatorDep
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _catalog_grant(conn, op)
        if not await assurance.resolve(
            conn, str(exception_id), owner=op.principal_id, resolution=body.resolution
        ):
            raise ZidError("EXCEPTION_NOT_OPEN", "No open exception with that id.", http_status=404)
    return {"exception_id": str(exception_id), "status": "RESOLVED"}


@router.get("/v1/commercial/payment-events")
async def payment_events(
    request: Request, op: OperatorDep, resolved: bool = Query(default=False)
) -> dict[str, Any]:
    """What the payment processor reported that needs a person: refunds, disputes, money for an invoice that cannot
    take it, and payments naming no invoice (CR-041). Resolved items too when `resolved=true`."""
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _catalog_grant(conn, op)
        return {"items": await payment_event_queue(conn, include_resolved=resolved)}


@router.post("/v1/commercial/payment-events/{event_id}:resolve")
async def resolve_payment_event_route(
    event_id: str, body: ResolveBody, request: Request, op: OperatorDep
) -> dict[str, Any]:
    if not re.fullmatch(r"evt_[A-Za-z0-9_]{6,100}", event_id):
        raise ZidError("EVENT_NOT_FOUND", "No open payment event with that id.", http_status=404)
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _catalog_grant(conn, op)
        if not await resolve_payment_event(
            conn, event_id, operator=op.principal_id, resolution=body.resolution
        ):
            raise ZidError(
                "EVENT_NOT_FOUND", "No open payment event with that id.", http_status=404
            )
    return {"event_id": event_id, "status": "RESOLVED"}


class RefundCreditBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Stripe reports the TOTAL refunded on a charge, so a second refund event repeats the first: the operator
    # confirms the amount still to be credited rather than the platform assuming it.
    amount: Decimal = Field(gt=0, max_digits=18, decimal_places=2)


@router.post("/v1/commercial/payment-events/{event_id}:credit-note", status_code=201)
async def credit_refund(
    event_id: str, body: RefundCreditBody, request: Request, op: OperatorDep
) -> dict[str, Any]:
    """Turn a refund Stripe reported into a credit note on the invoice it refunded, and close the review item, in one
    transaction (CR-041). Only a refund that names the company's own invoice qualifies, and the credit can never
    exceed what is left of that invoice (the credit-note rule itself)."""
    if not re.fullmatch(r"evt_[A-Za-z0-9_]{6,100}", event_id):
        raise ZidError("EVENT_NOT_FOUND", "No open refund with that id.", http_status=404)
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        found = await open_payment_refund(conn, event_id)
        if found is None:
            raise ZidError("EVENT_NOT_FOUND", "No open refund with that id.", http_status=404)
        tenant, invoice = str(found["tenant_id"]), str(found["invoice_id"])
        await _require(conn, op, "commercial.billing.manage", tenant, f"tenant:{tenant}")
        async with in_tenant(conn, tenant):
            try:
                note = await invoicing.credit_note(
                    conn,
                    invoice,
                    amount=body.amount,
                    reason_code="CUSTOMER_REFUND",
                    reverses_line=None,
                    issuer=op.principal_id,
                )
            except InvoicingError as e:
                raise _inv_fail(e) from e
        await resolve_payment_event(
            conn,
            event_id,
            operator=op.principal_id,
            resolution=f"Credit note {note['number']} for {body.amount}",
        )
    return note


@router.post("/v1/commercial/tenants/{tenant_id}/close")
async def close_month(
    tenant_id: uuid.UUID, body: PeriodQuery, request: Request, op: OperatorDep
) -> dict[str, Any]:
    """Freeze, rate and draft one tenant-month; resumable. Issuing stays a second person's step."""
    tenant = str(tenant_id)
    counters = getattr(request.app.state, "commercial_counters", {})
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _require(conn, op, "commercial.billing.manage", tenant, f"tenant:{tenant}")
        async with in_tenant(conn, tenant):
            connections = {k: await f(conn) for k, f in counters.items()}
            try:
                return await assurance.close_month(
                    conn,
                    tenant_id=tenant,
                    month=_month(body.month),
                    connections=connections,
                    issuing_entity=request.app.state.commercial_issuing_entity,
                    actor=op.principal_id,
                )
            except (BillableError, RatingError, InvoicingError) as e:
                raise ZidError(e.code, e.detail or e.code, http_status=409) from e


@router.get("/v1/commercial/tenants/{tenant_id}/audit-pack")
async def audit_pack(
    tenant_id: uuid.UUID,
    request: Request,
    op: OperatorDep,
    month: Annotated[str, Query(pattern=r"^\d{4}-\d{2}$")],
) -> dict[str, Any]:
    tenant = str(tenant_id)
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _require(conn, op, "commercial.billing.manage", tenant, f"tenant:{tenant}")
        async with in_tenant(conn, tenant):
            pack = await assurance.audit_pack(conn, tenant, _month(month))
    # Signed with the evidence signing key, outside the database transaction: a signature failure
    # leaves nothing half-done, and the pack is verifiable offline against the evidence public keys.
    runtime = current_runtime()
    return assurance.sign_audit_pack(runtime.signer, pack, issuer=runtime.cell_id)


class VerifyPackBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pack: dict[str, Any]


@router.post("/v1/commercial/audit-pack:verify")
async def verify_audit_pack(body: VerifyPackBody, op: OperatorDep) -> dict[str, Any]:
    """Check a signed audit pack against the trusted evidence keys: signature, unaltered body, bundle digest."""
    try:
        payload = assurance.verify_audit_pack(body.pack, keys=current_runtime().trusted_keys())
    except ValueError as e:
        return {"valid": False, "reason": str(e).split(":")[0]}
    return {"valid": True, "kid": body.pack["signature"]["kid"], "issuer": payload.get("iss")}


@router.get("/v1/commercial/tenants/{tenant_id}/journal")
async def journal(
    tenant_id: uuid.UUID,
    request: Request,
    op: OperatorDep,
    start: Annotated[datetime, Query()],
    end: Annotated[datetime, Query()],
    fmt: Annotated[Literal["json", "csv"], Query(alias="format")] = "json",
) -> Any:
    """Balanced journal lines for the period. `format=csv` is the file the finance team imports into the
    general ledger: this platform exports journals and never posts them."""
    tenant = str(tenant_id)
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _require(conn, op, "commercial.billing.manage", tenant, f"tenant:{tenant}")
        async with in_tenant(conn, tenant):
            out = await assurance.journal(conn, start, end)
    if fmt == "json":
        return out
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["account", "debit", "credit", "reference"])
    for ln in out["lines"]:
        w.writerow([ln["account"], ln["debit"] or "0", ln["credit"] or "0", ln["reference"]])
    return Response(
        buf.getvalue(),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="journal-{start:%Y%m%d}-{end:%Y%m%d}.csv"',
            "X-Journal-Balanced": str(out["balanced"]).lower(),
        },
    )


@router.get("/v1/commercial/kpis")
async def commercial_kpis(request: Request, op: OperatorDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await _catalog_grant(conn, op)
        return await assurance.kpis(conn)
